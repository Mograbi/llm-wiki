"""`wiki search`: rank vault pages for a query. No model in the loop.

Two rankers, fused when both are available:

- lexical: SQLite FTS5 with BM25 (title weighted, Porter stemming). Falls back
  to parsing pages from disk only if the sqlite build lacks FTS5.
- semantic: cosine over ##-section chunks, best chunk per page. Needs embedded
  chunks and a reachable embedder.

With both, results are fused by reciprocal rank (RRF), then each ranker's own
first choice is guaranteed a place in the top three. Plain RRF favours pages
both lists agree on, which buries a rare exact-token match that only BM25 can
see; the guarantee keeps that match visible. The mode is reported on the first
line so the caller knows what it is looking at.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from .index import Embedder, coverage, has_fts, open_db, unpack
from .vault import content_pages, parse_page

SNIPPET_LEN = 240
# \w is Unicode-aware, so Hebrew, Arabic, CJK and accented queries tokenize too.
TOKEN_RE = re.compile(r"\w[\w.-]*", re.UNICODE)
RRF_K = 60           # standard reciprocal-rank-fusion constant
CANDIDATES = 20      # per-ranker depth before fusion; deeper lists only add noise
GUARANTEE_WITHIN = 3 # each ranker's #1 is promoted into this many top slots

# Question words and glue. BM25's IDF already discounts these; stripping them
# keeps the lexical fallback and the FTS query clean.
STOPWORDS = frozenset("""
the and for are but not you all any can had her was one our out has his how its
may who did get let say she too use with what when where which while why does
this that these those they them then than there their from into onto over under
about after before again against between during through until above below being
been have having here just like make more most much must now off only other own
same some such very will would could should might shall also each few both
happens happen happened something anything nothing everything
is it to of in on at by an as or be do we my me
""".split())


@dataclass
class Hit:
    path: str
    section: str
    snippet: str
    score: float


@dataclass
class SearchResult:
    mode: str                          # "hybrid" | "semantic" | "lexical"
    hits: list[Hit] = field(default_factory=list)
    coverage: tuple[int, int] = (0, 0)  # (pages embedded, pages total)
    note: str = ""                     # why not hybrid, or partial-coverage warning


def _tokens(query: str) -> list[str]:
    seen: dict[str, None] = {}
    for t in TOKEN_RE.findall(query.lower()):
        t = t.strip(".-")
        if len(t) >= 2 and t not in STOPWORDS:
            seen.setdefault(t)
    return list(seen)


def _project_filter(db, project: str | None) -> set[str] | None:
    if not project:
        return None
    return {
        r["path"] for r in db.execute("SELECT path, projects FROM pages")
        if project in (r["projects"] or "").split(",")
    }


def search(vault: Path, db_path: Path, query: str, embedder: Embedder | None,
           k: int = 8, project: str | None = None) -> SearchResult:
    db = open_db(db_path)
    cov = coverage(db)
    allowed = _project_filter(db, project)
    tokens = _tokens(query)

    lexical = _lexical_ranked(db, vault, tokens, allowed)

    semantic: list[Hit] = []
    note = ""
    if embedder is None:
        note = ("semantic search off: no local embedder reachable"
                " (install Ollama, `ollama pull nomic-embed-text`, then `wiki reindex`)")
    elif cov[0] == 0:
        note = "semantic search off: nothing embedded yet — run `wiki reindex`"
    else:
        semantic = _semantic_ranked(db, query, embedder, allowed)
        if cov[0] < cov[1]:
            note = f"partial coverage: {cov[0]} of {cov[1]} pages embedded — run `wiki reindex`"
    db.close()

    if semantic and lexical:
        return SearchResult("hybrid", _fuse(semantic, lexical)[:k], cov, note)
    if semantic:
        return SearchResult("semantic", semantic[:k], cov, note)
    return SearchResult("lexical", lexical[:k], cov, note)


# -- rankers -----------------------------------------------------------------

def _semantic_ranked(db, query: str, embedder: Embedder, allowed: set[str] | None) -> list[Hit]:
    qvec = embedder.embed([query])[0]
    qnorm = math.sqrt(sum(x * x for x in qvec)) or 1.0
    best: dict[str, tuple[float, str, str]] = {}
    for row in db.execute("SELECT page, section, text, embedding FROM chunks"):
        if allowed is not None and row["page"] not in allowed:
            continue
        vec = unpack(row["embedding"])
        norm = math.sqrt(sum(x * x for x in vec)) or 1.0
        score = sum(a * b for a, b in zip(qvec, vec)) / (qnorm * norm)
        if row["page"] not in best or score > best[row["page"]][0]:
            best[row["page"]] = (score, row["section"], row["text"])
    ranked = sorted(best.items(), key=lambda kv: kv[1][0], reverse=True)[:CANDIDATES]
    return [Hit(p, sec, _snip(text), round(sc, 4)) for p, (sc, sec, text) in ranked]


def _lexical_ranked(db, vault: Path, tokens: list[str], allowed: set[str] | None) -> list[Hit]:
    if not tokens:
        return []
    if has_fts(db):
        return _fts_ranked(db, tokens, allowed)
    return _disk_ranked(vault, tokens, allowed)


def _fts_ranked(db, tokens: list[str], allowed: set[str] | None) -> list[Hit]:
    # Each token quoted so punctuation (802.1x, nvv4l2decoder) cannot break the query.
    match = " OR ".join('"' + t.replace('"', '""') + '"' for t in tokens)
    rows = db.execute(
        "SELECT path, snippet(pages_fts, 2, '', '', '…', 24) AS snip,"
        " bm25(pages_fts, 0.0, 5.0, 1.0) AS rank"
        " FROM pages_fts WHERE pages_fts MATCH ? ORDER BY rank LIMIT ?",
        (match, CANDIDATES * 3),
    ).fetchall()
    hits = []
    for r in rows:
        if allowed is not None and r["path"] not in allowed:
            continue
        # bm25() is negative-better; flip so bigger is better like the other ranker
        hits.append(Hit(r["path"], "", _snip(r["snip"]), round(-r["rank"], 4)))
        if len(hits) >= CANDIDATES:
            break
    return hits


def _disk_ranked(vault: Path, tokens: list[str], allowed: set[str] | None) -> list[Hit]:
    """Fallback for sqlite builds without FTS5: token overlap, title weighted."""
    scored: list[tuple[float, str, str]] = []
    for rel in content_pages(vault):
        if allowed is not None and rel not in allowed:
            continue
        page = parse_page(vault, rel)
        title, body = page.title.lower(), page.body.lower()
        score, first_line = 0.0, ""
        for t in tokens:
            n = body.count(t)
            if n:
                score += min(n, 5)
                first_line = first_line or _line_with(page.body, t)
            if t in title:
                score += 3
        if score > 0:
            scored.append((score, rel, first_line or page.title))
    scored.sort(key=lambda s: (-s[0], s[1]))
    return [Hit(rel, "", _snip(snip), float(sc)) for sc, rel, snip in scored[:CANDIDATES]]


# -- fusion -------------------------------------------------------------------

def _fuse(semantic: list[Hit], lexical: list[Hit]) -> list[Hit]:
    """Reciprocal rank fusion with a top-1 guarantee per ranker.

    Section/snippet come from the semantic hit when the page appears there (it
    knows which section matched), else from FTS. Measured on a 465-page vault,
    the guarantee is what lifts top-8 recall to 15/15: without it, a page that
    only the lexical ranker finds (an exact rare token) is outscored by pages
    that appear in both lists at middling ranks."""
    score: dict[str, float] = {}
    detail: dict[str, Hit] = {}
    for rank, h in enumerate(semantic, 1):
        score[h.path] = score.get(h.path, 0.0) + 1.0 / (RRF_K + rank)
        detail.setdefault(h.path, h)
    for rank, h in enumerate(lexical, 1):
        score[h.path] = score.get(h.path, 0.0) + 1.0 / (RRF_K + rank)
        detail.setdefault(h.path, h)
    order = sorted(score, key=lambda p: (-score[p], p))
    for first in (semantic[0].path, lexical[0].path):
        if first not in order[:GUARANTEE_WITHIN]:
            order.remove(first)
            order.insert(GUARANTEE_WITHIN - 1, first)
    return [Hit(p, detail[p].section, detail[p].snippet, round(score[p], 4)) for p in order]


# -- helpers ------------------------------------------------------------------

def _line_with(body: str, token: str) -> str:
    for line in body.splitlines():
        if token in line.lower():
            return line.strip()
    return ""


def _snip(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= SNIPPET_LEN else text[:SNIPPET_LEN - 1] + "…"

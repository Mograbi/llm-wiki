"""Derived sqlite index: pages, wikilink graph, and (optionally) embedded chunks.

Disposable by design — the vault is the source of truth. Used to regenerate
_meta/index.md, _meta/index/*.md and _meta/hot.md, and to back `wiki search`.

Incremental: pages are keyed by content hash; only changed/new pages are
re-parsed and re-embedded, deleted pages' rows are removed. Embeddings are
float32 BLOBs searched by brute-force cosine; at a few thousand chunks that is
milliseconds and needs no vector database.

Coverage is allowed to be partial: if the embedder is unreachable during a
reindex, unchanged pages keep their old chunks and changed pages lose theirs,
so search stays honest about what it can see.

Several agent sessions may run the CLI at once against the same vault and the
same cache. The index is WAL-mode sqlite with a busy timeout; a build does all
of its reading, parsing and embedding first and then writes in one short
transaction, so the write lock is held for milliseconds, never for the length
of an embedding call. An advisory file lock around the build keeps two
processes from embedding the same pages twice.
"""

from __future__ import annotations

import contextlib
import datetime
import hashlib
import re
import sqlite3
import struct
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from .fsutil import UnsafePath, rename_nofollow, write_text_atomic
from .vault import (MAX_PAGE_BYTES, Page, normalize_target, parse_page, scalar,
                    scalar_list, scan_pages)

SCHEMA = """
CREATE TABLE IF NOT EXISTS pages (
    path TEXT PRIMARY KEY,
    hash TEXT NOT NULL,
    type TEXT,
    title TEXT,
    projects TEXT,
    updated TEXT,
    status TEXT
);
CREATE TABLE IF NOT EXISTS links (
    src TEXT NOT NULL,
    dst TEXT NOT NULL,
    PRIMARY KEY (src, dst)
);
CREATE INDEX IF NOT EXISTS links_dst ON links (dst);
CREATE TABLE IF NOT EXISTS chunks (
    page TEXT NOT NULL,
    section TEXT NOT NULL,
    text TEXT NOT NULL,
    embedding BLOB NOT NULL
);
CREATE INDEX IF NOT EXISTS chunks_page ON chunks (page);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# Full-text index for lexical search (BM25). FTS5 ships with nearly every
# Python build; when it is missing, search falls back to parsing pages from disk.
FTS_SCHEMA = """
CREATE VIRTUAL TABLE IF NOT EXISTS pages_fts
USING fts5(path UNINDEXED, title, body, tokenize='porter unicode61');
"""

BUSY_TIMEOUT_S = 30.0   # how long a writer waits for another session's transaction


class Embedder(Protocol):
    identity: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def open_db(db_path: Path) -> sqlite3.Connection:
    """Open the index in autocommit mode: reads never hold a transaction, and the
    one writer (build_index) brackets its own BEGIN IMMEDIATE / COMMIT."""
    db = sqlite3.connect(db_path, timeout=BUSY_TIMEOUT_S, isolation_level=None)
    db.row_factory = sqlite3.Row
    with contextlib.suppress(sqlite3.OperationalError):
        db.execute("PRAGMA journal_mode=WAL")   # persistent; fails harmlessly on read-only media
    db.execute("PRAGMA synchronous=NORMAL")
    db.executescript(SCHEMA)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(pages)")}
    if "stat" not in cols:   # older index: add the cheap-change-detection column
        db.execute("ALTER TABLE pages ADD COLUMN stat TEXT")
    if "tldr" not in cols:   # older index: pages are re-parsed lazily to fill it
        db.execute("ALTER TABLE pages ADD COLUMN tldr TEXT")
    try:
        db.executescript(FTS_SCHEMA)
    except sqlite3.OperationalError:
        pass  # this sqlite build lacks FTS5; lexical search parses from disk instead
    return db


def has_fts(db: sqlite3.Connection) -> bool:
    return db.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'pages_fts'"
    ).fetchone() is not None


def _stat(path: Path) -> str:
    st = path.stat()
    return f"{st.st_mtime_ns}:{st.st_size}"


def coverage(db: sqlite3.Connection) -> tuple[int, int]:
    """(pages with embedded chunks, total pages)."""
    embedded = db.execute("SELECT COUNT(DISTINCT page) FROM chunks").fetchone()[0]
    total = db.execute("SELECT COUNT(*) FROM pages").fetchone()[0]
    return embedded, total


@contextlib.contextmanager
def build_lock(db_path: Path):
    """Advisory inter-process lock so two sessions do not parse and embed the same
    changes at once. Blocking: a build is seconds, an embed run minutes at most.
    Platforms without fcntl (Windows) fall back to sqlite's own busy timeout."""
    try:
        import fcntl
    except ImportError:          # pragma: no cover - not Linux/macOS
        yield
        return
    lock_path = db_path.with_name(db_path.name + ".lock")
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


@dataclass
class _Plan:
    """What one page needs, decided before any write."""
    rel: str
    stat: str
    digest: str | None = None
    page: Page | None = None
    changed: bool = False
    stat_only: bool = False        # content same, mtime/size moved: refresh stat
    fill_tldr: bool = False        # content same, older index row lacks the tldr
    embed: bool = False
    fts: bool = False
    vectors: list[tuple[str, str, list[float]]] = field(default_factory=list)


def build_index(vault: Path, db_path: Path, embedder: Embedder | None = None) -> dict:
    """Sync the index with the vault.

    Returns {'updated', 'removed', 'embedded', 'coverage': (embedded_pages, total)}.

    Change detection is two-stage: a file whose mtime and size are unchanged is
    skipped without being read (network mounts make reads expensive); otherwise
    its content hash decides. With an embedder, changed pages (and any page
    missing chunks) are embedded; a change of embedder identity re-embeds
    everything. Without one, changed pages drop their now-stale chunks.

    All reading, parsing and embedding happens before the write transaction, so
    another session's `wiki search` or `wiki lint` is never blocked for longer
    than the writes themselves take.
    """
    with build_lock(db_path):
        return _build_index(vault, db_path, embedder)


def _build_index(vault: Path, db_path: Path, embedder: Embedder | None) -> dict:
    db = open_db(db_path)
    fts = has_fts(db)

    # ---- phase 1: read the index and the vault, decide, parse, embed. No writes.
    reembed_all = False
    if embedder is not None:
        row = db.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()
        reembed_all = bool(row and row["value"] != embedder.identity)

    known = {r["path"]: (r["hash"], r["stat"], r["tldr"])
             for r in db.execute("SELECT path, hash, stat, tldr FROM pages")}
    have_chunks = {r["page"] for r in db.execute("SELECT DISTINCT page FROM chunks")}
    have_fts = ({r["path"] for r in db.execute("SELECT path FROM pages_fts")}
                if fts else set())
    current, skipped = scan_pages(vault)
    removed = sorted(set(known) - set(current))

    plans: list[_Plan] = []
    oversized: list[str] = []
    for rel in current:
        file = vault / rel
        stat = _stat(file)
        if int(stat.rsplit(":", 1)[1]) > MAX_PAGE_BYTES:
            oversized.append(rel)
            continue
        old_hash, old_stat, old_tldr = known.get(rel, (None, None, None))
        need_embed_missing = embedder is not None and (reembed_all or rel not in have_chunks)
        need_fts_missing = fts and rel not in have_fts
        need_tldr = old_hash is not None and old_tldr is None
        if old_stat == stat and not (need_embed_missing or need_fts_missing or need_tldr):
            continue  # unchanged on disk, nothing missing: skip without reading
        raw = file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        changed = old_hash != digest
        plan = _Plan(rel=rel, stat=stat, digest=digest, changed=changed)
        plan.stat_only = not changed and old_stat != stat
        plan.fill_tldr = not changed and need_tldr
        plan.embed = embedder is not None and (changed or need_embed_missing)
        plan.fts = fts and (changed or need_fts_missing)
        if changed or plan.embed or plan.fts or plan.fill_tldr:
            plan.page = parse_page(vault, rel)
        if plan.embed:
            plan.vectors = _embed_sections(plan.page, embedder)
        if changed or plan.stat_only or plan.fill_tldr or plan.embed or plan.fts:
            plans.append(plan)

    # ---- phase 2: one short write transaction.
    db.execute("BEGIN IMMEDIATE")
    try:
        if embedder is not None:
            if reembed_all:
                db.execute("DELETE FROM chunks")
            db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embedder', ?)",
                       (embedder.identity,))
        for rel in removed + oversized:
            _drop_page(db, rel, fts)
        updated = embedded = 0
        for p in plans:
            if p.stat_only:
                db.execute("UPDATE pages SET stat = ? WHERE path = ?", (p.stat, p.rel))
            if p.fill_tldr:
                db.execute("UPDATE pages SET tldr = ? WHERE path = ?", (p.page.tldr, p.rel))
            if p.embed:
                db.execute("DELETE FROM chunks WHERE page = ?", (p.rel,))
                db.executemany(
                    "INSERT INTO chunks (page, section, text, embedding) VALUES (?, ?, ?, ?)",
                    [(p.rel, heading, text, pack(vec)) for heading, text, vec in p.vectors],
                )
                embedded += 1
            if p.fts:
                db.execute("DELETE FROM pages_fts WHERE path = ?", (p.rel,))
                db.execute("INSERT INTO pages_fts (path, title, body) VALUES (?, ?, ?)",
                           (p.rel, p.page.title, p.page.body))
            if not p.changed:
                continue
            fm = p.page.frontmatter
            db.execute(
                "INSERT OR REPLACE INTO pages"
                " (path, hash, stat, type, title, projects, updated, status, tldr)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    p.rel, p.digest, p.stat,
                    scalar(fm.get("type")),
                    p.page.title[:300],
                    ",".join(scalar_list(fm.get("projects"))),
                    scalar(fm.get("updated") or fm.get("ingested") or fm.get("asked")) or "",
                    scalar(fm.get("status")),
                    p.page.tldr,
                ),
            )
            db.execute("DELETE FROM links WHERE src = ?", (p.rel,))
            db.executemany("INSERT OR IGNORE INTO links (src, dst) VALUES (?, ?)",
                           [(p.rel, dst) for dst in p.page.wikilinks])
            if embedder is None:
                # content changed but we cannot re-embed: stale chunks must go
                db.execute("DELETE FROM chunks WHERE page = ?", (p.rel,))
            updated += 1
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
                   (str(time.time()),))
        db.execute("COMMIT")
    except BaseException:
        with contextlib.suppress(sqlite3.Error):
            db.execute("ROLLBACK")
        raise
    cov = coverage(db)
    db.close()
    return {"updated": updated, "removed": len(removed), "embedded": embedded, "coverage": cov,
            "oversized": oversized, "skipped": skipped}


def _drop_page(db: sqlite3.Connection, rel: str, fts: bool) -> None:
    db.execute("DELETE FROM pages WHERE path = ?", (rel,))
    db.execute("DELETE FROM links WHERE src = ?", (rel,))
    db.execute("DELETE FROM chunks WHERE page = ?", (rel,))
    if fts:
        db.execute("DELETE FROM pages_fts WHERE path = ?", (rel,))


def seconds_since_sync(db_path: Path) -> float | None:
    """Age of the last build_index, or None if the index has never been built."""
    if not db_path.exists():
        return None
    db = open_db(db_path)
    row = db.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
    db.close()
    return time.time() - float(row["value"]) if row else None


def _embed_sections(page: Page, embedder: Embedder) -> list[tuple[str, str, list[float]]]:
    sections = page.sections()
    vectors = embedder.embed(
        [f"{page.title} — {heading}\n{text}" if heading else f"{page.title}\n{text}"
         for heading, text in sections]
    )
    return [(heading, text, vec) for (heading, text), vec in zip(sections, vectors)]


# --------------------------------------------------------------------------- generated pages

GENERATED_MARKER = "GENERATED by `wiki reindex`"

INDEX_MD_HEADER = """---
type: meta
generated: true
---

<!-- """ + GENERATED_MARKER + """ — do not hand-edit. -->

"""

TYPE_ORDER = ("project", "entity", "source", "query", "person", "agent")
PLURAL = {"entity": "Entities", "person": "People", "query": "Queries"}
HOT_DAYS = 7
HOT_MAX_LOG_LINES = 60
HOT_MAX_PAGES = 40
HOT_MAX_LINE_CHARS = 200
LOG_LINE_RE = re.compile(r"^(\d{4}-\d{2}-\d{2}) ")


def plural(page_type: str) -> str:
    return PLURAL.get(page_type, page_type.capitalize() + "s")


@dataclass
class IndexResult:
    path: Path
    archived: Path | None = None   # a hand-written index.md moved aside, if any
    untyped: int = 0               # pages with no recognized `type:` frontmatter
    shards: list[Path] = field(default_factory=list)
    hot: Path | None = None


def _archive_handwritten_index(vault: Path) -> Path | None:
    """Never overwrite an index.md a human wrote. Move it aside first."""
    target = vault / "_meta" / "index.md"
    if target.is_symlink():
        raise UnsafePath(f"refusing to touch symlink: {target}")
    if not target.exists():
        return None
    if GENERATED_MARKER in target.read_text(encoding="utf-8")[:400]:
        return None
    archive = vault / "_meta" / "index-archive.md"
    if archive.exists():
        stamp = datetime.date.today().isoformat()
        archive = vault / "_meta" / f"index-archive-{stamp}.md"
        n = 2
        while archive.exists():
            archive = vault / "_meta" / f"index-archive-{stamp}-{n}.md"
            n += 1
    rename_nofollow(target, archive)
    return archive


def _entry(row, inbound: dict[str, int], with_tldr: bool = True) -> str:
    stem = Path(row["path"]).stem
    n = inbound.get(normalize_target(stem), 0)
    salience = f" ({n}←)" if n else ""
    projects = f" [{row['projects']}]" if row["projects"] else ""
    tldr = f" — {row['tldr']}" if with_tldr and row["tldr"] else ""
    return f"- [[{stem}]] — {row['title']}{tldr}{projects}{salience}\n"


def generate_index_md(vault: Path, db_path: Path) -> IndexResult:
    """Regenerate the index pages from the index db.

    `_meta/index.md` is a short overview: counts by type and by project, the
    project hubs, and links to one shard per type. `_meta/index/<type>.md` lists
    every page of that type with its title, first prose line, projects and
    inbound-link count, so an agent can route from a shard without opening
    pages. All writes are atomic. A hand-written index.md is archived rather
    than overwritten."""
    archived = _archive_handwritten_index(vault)
    db = open_db(db_path)
    inbound = {r["dst"]: r["c"] for r in db.execute("SELECT dst, COUNT(*) c FROM links GROUP BY dst")}
    placeholders = ",".join("?" * len(TYPE_ORDER))
    by_type = {
        t: db.execute("SELECT path, title, projects, updated, tldr FROM pages WHERE type = ?"
                      " ORDER BY path", (t,)).fetchall()
        for t in TYPE_ORDER
    }
    other = db.execute(
        f"SELECT path, title, projects, updated, tldr FROM pages"
        f" WHERE type IS NULL OR type NOT IN ({placeholders}) ORDER BY path", TYPE_ORDER,
    ).fetchall()
    per_project: dict[str, int] = {}
    for r in db.execute("SELECT projects FROM pages"):
        for tag in (r["projects"] or "").split(","):
            if tag:
                per_project[tag] = per_project.get(tag, 0) + 1
    db.close()

    index_dir = vault / "_meta" / "index"
    index_dir.mkdir(exist_ok=True)
    shards: list[Path] = []
    overview = [INDEX_MD_HEADER, "# Index\n\n",
                "Overview only. Each type has a shard with one line per page"
                " (title, first line, projects, inbound links); `wiki search` ranks"
                " pages for a question and is the better entry point.\n\n",
                "## Pages by type\n\n"]
    for t in TYPE_ORDER:
        rows = by_type[t]
        shard = index_dir / f"{plural(t).lower()}.md"
        if not rows:
            if shard.exists() and not shard.is_symlink():
                shard.unlink()
            continue
        body = [INDEX_MD_HEADER, f"# {plural(t)} ({len(rows)})\n\n"]
        body += [_entry(r, inbound) for r in rows]
        write_text_atomic(shard, "".join(body))
        shards.append(shard)
        overview.append(f"- [[_meta/index/{shard.stem}|{plural(t)}]] — {len(rows)}\n")
    if other:
        shard = index_dir / "other.md"
        body = [INDEX_MD_HEADER, f"# Other ({len(other)})\n\n",
                "<!-- pages with no recognized `type:` — see _meta/schema.md -->\n\n"]
        body += [_entry(r, inbound) for r in other]
        write_text_atomic(shard, "".join(body))
        shards.append(shard)
        overview.append(f"- [[_meta/index/other|Other]] — {len(other)} (no recognized `type:`)\n")
    elif (index_dir / "other.md").exists():
        (index_dir / "other.md").unlink()

    if per_project:
        overview.append("\n## Pages by project\n\n")
        for tag, n in sorted(per_project.items(), key=lambda kv: (-kv[1], kv[0])):
            overview.append(f"- `{tag}` — {n}\n")
    if by_type["project"]:
        overview.append("\n## Project hubs\n\n")
        overview += [_entry(r, inbound) for r in by_type["project"]]
    overview.append("\nRecent activity: [[_meta/hot|hot]].\n")

    target = vault / "_meta" / "index.md"
    write_text_atomic(target, "".join(overview))
    hot = generate_hot_md(vault, db_path)
    return IndexResult(path=target, archived=archived, untyped=len(other), shards=shards, hot=hot)


def _recent_log_lines(vault: Path, since: datetime.date) -> list[str]:
    """Dated lines from the monthly log shards on or after `since`, newest shard
    last, bounded. Continuation lines are dropped: the index wants one line per act."""
    log_dir = vault / "_meta" / "log"
    if not log_dir.is_dir():
        return []
    out: list[str] = []
    for shard in sorted(log_dir.glob("*.md")):
        if shard.is_symlink() or not shard.is_file():
            continue
        if shard.stem < since.strftime("%Y-%m"):
            continue
        for line in shard.read_text(encoding="utf-8", errors="replace").splitlines():
            m = LOG_LINE_RE.match(line)
            if m and m.group(1) >= since.isoformat():
                line = line.rstrip()
                if len(line) > HOT_MAX_LINE_CHARS:
                    line = line[:HOT_MAX_LINE_CHARS].rsplit(" ", 1)[0] + "…"
                out.append(line)
    return out[-HOT_MAX_LOG_LINES:]


def generate_hot_md(vault: Path, db_path: Path, today: datetime.date | None = None) -> Path:
    """`_meta/hot.md`: what moved in the last week. The recent log lines and
    the pages whose `updated:`/`ingested:`/`asked:` falls in the window, grouped by
    type with their first line. Small by construction, so a session can read it
    once at the start of vault work instead of the whole index."""
    today = today or datetime.date.today()
    since = today - datetime.timedelta(days=HOT_DAYS)
    db = open_db(db_path)
    inbound = {r["dst"]: r["c"] for r in db.execute("SELECT dst, COUNT(*) c FROM links GROUP BY dst")}
    rows = db.execute(
        "SELECT path, type, title, projects, updated, tldr FROM pages"
        " WHERE updated >= ? ORDER BY updated DESC, path LIMIT ?",
        (since.isoformat(), HOT_MAX_PAGES),
    ).fetchall()
    db.close()
    out = [INDEX_MD_HEADER, f"# Hot — last {HOT_DAYS} days (as of {today.isoformat()})\n\n"]
    by_type: dict[str, list] = {}
    for r in rows:
        by_type.setdefault(r["type"] or "other", []).append(r)
    out.append("## Recently touched pages\n\n")
    if not rows:
        out.append("_none_\n\n")
    for t in (*TYPE_ORDER, "other"):
        group = by_type.get(t)
        if not group:
            continue
        out.append(f"### {plural(t) if t != 'other' else 'Other'}\n\n")
        for r in group:
            out.append(f"- {r['updated']} " + _entry(r, inbound)[2:])
        out.append("\n")
    lines = _recent_log_lines(vault, since)
    out.append(f"## Log, last {HOT_DAYS} days ({len(lines)} lines)\n\n")
    out += [f"{line}\n" for line in lines] or ["_none_\n"]
    target = vault / "_meta" / "hot.md"
    write_text_atomic(target, "".join(out))
    return target

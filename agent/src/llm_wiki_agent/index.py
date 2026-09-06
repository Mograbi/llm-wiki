"""Derived sqlite index: pages, wikilink graph, and (optionally) embedded chunks.

Disposable by design — the vault is the source of truth. Used to regenerate
_meta/index.md and to back `wiki search`.

Incremental: pages are keyed by content hash; only changed/new pages are
re-parsed and re-embedded, deleted pages' rows are removed. Embeddings are
float32 BLOBs searched by brute-force cosine; at a few thousand chunks that is
milliseconds and needs no vector database.

Coverage is allowed to be partial: if the embedder is unreachable during a
reindex, unchanged pages keep their old chunks and changed pages lose theirs,
so search stays honest about what it can see.
"""

from __future__ import annotations

import datetime
import hashlib
import sqlite3
import struct
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .vault import content_pages, normalize_target, parse_page

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


class Embedder(Protocol):
    identity: str
    def embed(self, texts: list[str]) -> list[list[float]]: ...


def pack(vec: list[float]) -> bytes:
    return struct.pack(f"{len(vec)}f", *vec)


def unpack(blob: bytes) -> list[float]:
    return list(struct.unpack(f"{len(blob) // 4}f", blob))


def open_db(db_path: Path) -> sqlite3.Connection:
    db = sqlite3.connect(db_path)
    db.row_factory = sqlite3.Row
    db.executescript(SCHEMA)
    cols = {r["name"] for r in db.execute("PRAGMA table_info(pages)")}
    if "stat" not in cols:   # older index: add the cheap-change-detection column
        db.execute("ALTER TABLE pages ADD COLUMN stat TEXT")
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


def build_index(vault: Path, db_path: Path, embedder: Embedder | None = None) -> dict:
    """Sync the index with the vault.

    Returns {'updated', 'removed', 'embedded', 'coverage': (embedded_pages, total)}.

    Change detection is two-stage: a file whose mtime and size are unchanged is
    skipped without being read (network mounts make reads expensive); otherwise
    its content hash decides. With an embedder, changed pages (and any page
    missing chunks) are embedded; a change of embedder identity re-embeds
    everything. Without one, changed pages drop their now-stale chunks.
    """
    db = open_db(db_path)

    reembed_all = False
    if embedder is not None:
        row = db.execute("SELECT value FROM meta WHERE key = 'embedder'").fetchone()
        if row and row["value"] != embedder.identity:
            reembed_all = True
            db.execute("DELETE FROM chunks")
        db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('embedder', ?)",
                   (embedder.identity,))

    known = {r["path"]: (r["hash"], r["stat"]) for r in db.execute("SELECT path, hash, stat FROM pages")}
    current = content_pages(vault)

    removed = set(known) - set(current)
    for path in removed:
        db.execute("DELETE FROM pages WHERE path = ?", (path,))
        db.execute("DELETE FROM links WHERE src = ?", (path,))
        db.execute("DELETE FROM chunks WHERE page = ?", (path,))
        if has_fts(db):
            db.execute("DELETE FROM pages_fts WHERE path = ?", (path,))

    have_chunks = {r["page"] for r in db.execute("SELECT DISTINCT page FROM chunks")}
    have_fts = ({r["path"] for r in db.execute("SELECT path FROM pages_fts")}
                if has_fts(db) else set(current))

    updated = 0
    embedded = 0
    for rel in current:
        file = vault / rel
        stat = _stat(file)
        old_hash, old_stat = known.get(rel, (None, None))
        needs_embed_missing = embedder is not None and (reembed_all or rel not in have_chunks)
        needs_fts_missing = rel not in have_fts
        if old_stat == stat and not needs_embed_missing and not needs_fts_missing:
            continue  # unchanged on disk, nothing missing: skip without reading
        raw = file.read_bytes()
        digest = hashlib.sha256(raw).hexdigest()
        changed = old_hash != digest
        if not changed and old_stat != stat:
            db.execute("UPDATE pages SET stat = ? WHERE path = ?", (stat, rel))
        needs_embed = embedder is not None and (changed or needs_embed_missing)
        if not changed and not needs_embed and not needs_fts_missing:
            continue
        page = parse_page(vault, rel)
        if needs_embed:
            _embed_page(db, rel, page, embedder)
            embedded += 1
        if has_fts(db) and (changed or needs_fts_missing):
            db.execute("DELETE FROM pages_fts WHERE path = ?", (rel,))
            db.execute("INSERT INTO pages_fts (path, title, body) VALUES (?, ?, ?)",
                       (rel, page.title, page.body))
        if not changed:
            continue
        fm = page.frontmatter
        projects = fm.get("projects") or []
        if isinstance(projects, str):
            projects = [projects]
        db.execute(
            "INSERT OR REPLACE INTO pages (path, hash, stat, type, title, projects, updated, status)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                rel,
                digest,
                stat,
                fm.get("type"),
                page.title,
                ",".join(str(p) for p in projects),
                str(fm.get("updated") or fm.get("ingested") or fm.get("asked") or ""),
                fm.get("status"),
            ),
        )
        db.execute("DELETE FROM links WHERE src = ?", (rel,))
        db.executemany(
            "INSERT OR IGNORE INTO links (src, dst) VALUES (?, ?)",
            [(rel, dst) for dst in page.wikilinks],
        )
        if embedder is None:
            # content changed but we cannot re-embed: stale chunks must go
            db.execute("DELETE FROM chunks WHERE page = ?", (rel,))
        updated += 1

    db.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('last_sync', ?)",
               (str(time.time()),))
    db.commit()
    cov = coverage(db)
    db.close()
    return {"updated": updated, "removed": len(removed), "embedded": embedded, "coverage": cov}


def seconds_since_sync(db_path: Path) -> float | None:
    """Age of the last build_index, or None if the index has never been built."""
    if not db_path.exists():
        return None
    db = open_db(db_path)
    row = db.execute("SELECT value FROM meta WHERE key = 'last_sync'").fetchone()
    db.close()
    return time.time() - float(row["value"]) if row else None


def _embed_page(db: sqlite3.Connection, rel: str, page, embedder: Embedder) -> None:
    sections = page.sections()
    vectors = embedder.embed(
        [f"{page.title} — {heading}\n{text}" if heading else f"{page.title}\n{text}"
         for heading, text in sections]
    )
    db.execute("DELETE FROM chunks WHERE page = ?", (rel,))
    db.executemany(
        "INSERT INTO chunks (page, section, text, embedding) VALUES (?, ?, ?, ?)",
        [(rel, heading, text, pack(vec)) for (heading, text), vec in zip(sections, vectors)],
    )


GENERATED_MARKER = "GENERATED by `wiki reindex`"

INDEX_MD_HEADER = """---
type: meta
generated: true
---

<!-- """ + GENERATED_MARKER + """ — do not hand-edit. -->

# Index

"""

TYPE_ORDER = ("project", "entity", "source", "query", "person")


@dataclass
class IndexResult:
    path: Path
    archived: Path | None = None   # a hand-written index.md moved aside, if any
    untyped: int = 0               # pages with no recognized `type:` frontmatter


def _archive_handwritten_index(vault: Path) -> Path | None:
    """Never overwrite an index.md a human wrote. Move it aside first."""
    target = vault / "_meta" / "index.md"
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
    target.rename(archive)
    return archive


def generate_index_md(vault: Path, db_path: Path) -> IndexResult:
    """Regenerate _meta/index.md from the index db (grouped by type,
    inbound-link counts as a salience hint). A hand-written index.md is
    archived rather than overwritten."""
    archived = _archive_handwritten_index(vault)
    db = open_db(db_path)
    inbound = {
        r["dst"]: r["c"]
        for r in db.execute("SELECT dst, COUNT(*) c FROM links GROUP BY dst")
    }
    def render(rows) -> None:
        for r in rows:
            stem = Path(r["path"]).stem
            n = inbound.get(normalize_target(stem), 0)
            salience = f" ({n}←)" if n else ""
            projects = f" [{r['projects']}]" if r["projects"] else ""
            out.append(f"- [[{stem}]] — {r['title']}{projects}{salience}\n")
        out.append("\n")

    out = [INDEX_MD_HEADER]
    for page_type in TYPE_ORDER:
        rows = db.execute(
            "SELECT path, title, projects, updated FROM pages WHERE type = ?"
            " ORDER BY path",
            (page_type,),
        ).fetchall()
        if not rows:
            continue
        plural = {"entity": "Entities", "person": "People", "query": "Queries"}.get(
            page_type, page_type.capitalize() + "s"
        )
        out.append(f"## {plural} ({len(rows)})\n\n")
        render(rows)

    # Anything without a recognized type still belongs in the index.
    placeholders = ",".join("?" * len(TYPE_ORDER))
    other = db.execute(
        f"SELECT path, title, projects, updated FROM pages"
        f" WHERE type IS NULL OR type NOT IN ({placeholders}) ORDER BY path",
        TYPE_ORDER,
    ).fetchall()
    if other:
        out.append(f"## Other ({len(other)})\n\n")
        out.append("<!-- pages with no recognized `type:` — see _meta/schema.md -->\n\n")
        render(other)

    db.close()
    target = vault / "_meta" / "index.md"
    target.write_text("".join(out), encoding="utf-8")
    return IndexResult(path=target, archived=archived, untyped=len(other))

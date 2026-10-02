"""`wiki lint`: mechanical vault checks as SQL over the derived index. No model in the loop.

Orphans (no inbound wikilink from another page), broken wikilinks (target matches no page),
missing `projects:` on sources/entities/queries, and stale-active (status active, `updated:`
older than the threshold). Contradictions and index drift are the agent's judgment, not this.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Literal

from .fsutil import is_regular_file_inside
from .index import open_db
from .vault import normalize_target

Kind = Literal["orphan", "broken_link", "missing_projects", "stale_active"]
KINDS: tuple[Kind, ...] = ("broken_link", "orphan", "missing_projects", "stale_active")
NEEDS_PROJECT = ("source", "entity", "query")


@dataclass(frozen=True)
class Defect:
    kind: Kind
    page: str          # vault-relative path of the page carrying the defect
    target: str = ""   # normalized link target, for broken_link only

    @property
    def key(self) -> tuple[str, str, str]:
        return (self.kind, self.page, self.target)


def _stem(path: str) -> str:
    return normalize_target(Path(path).stem)


def _path_target_exists(vault: Path, target: str) -> bool:
    # Path-qualified links (`[[_meta/schema]]`, `[[notes/2026/analysis]]`) point outside
    # the indexed content dirs but are real pages.
    if "/" not in target or target.startswith("/") or ".." in target:
        return False
    # Not from our own walk, so the strict check: a plain file that resolves inside the vault.
    return is_regular_file_inside(vault / f"{target}.md", vault)


def mechanical_lint(vault: Path, db_path: Path, *, today: date | None = None,
                    stale_days: int = 180) -> list[Defect]:
    """Run the four checks against an up-to-date index. Caller is responsible for the sync."""
    today = today or date.today()
    db = open_db(db_path)
    pages = db.execute("SELECT path, type, projects, updated, status FROM pages").fetchall()
    links = db.execute("SELECT src, dst FROM links").fetchall()
    by_stem = {_stem(p[0]): p[0] for p in pages}
    inbound: dict[str, set[str]] = {p[0]: set() for p in pages}
    out: list[Defect] = []
    for src, dst in links:
        dst_n = normalize_target(dst)
        tgt = by_stem.get(dst_n)
        if tgt is None:
            if not _path_target_exists(vault, dst_n):
                out.append(Defect("broken_link", src, dst_n))
        elif tgt != src:
            inbound[tgt].add(src)
    for path, ptype, projects, updated, status in pages:
        if not inbound[path]:
            out.append(Defect("orphan", path))
        if ptype in NEEDS_PROJECT and not (projects or "").strip():
            out.append(Defect("missing_projects", path))
        if status == "active" and updated:
            try:
                age = (today - date.fromisoformat(str(updated)[:10])).days
            except ValueError:
                age = -1
            if age > stale_days:
                out.append(Defect("stale_active", path))
    return sorted(out, key=lambda d: (KINDS.index(d.kind), d.page, d.target))

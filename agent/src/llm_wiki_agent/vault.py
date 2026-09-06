"""Vault page parsing: frontmatter, titles, wikilinks, section chunks.

The vault is the source of truth; this module only reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

from .fsutil import is_regular_file_inside

CONTENT_DIRS = ("sources", "entities", "queries", "projects", "people")

# Bounded and newline-free so a page full of "[[" cannot make matching quadratic.
WIKILINK_RE = re.compile(r"\[\[([^\[\]|#\n]{1,200})(?:[#|][^\[\]\n]{0,200})?\]\]")
MAX_PAGE_BYTES = 2 * 1024 * 1024      # larger pages are skipped with a warning
MAX_FRONTMATTER_BYTES = 64 * 1024     # larger frontmatter is treated as malformed
MAX_FIELD_CHARS = 300                 # stored frontmatter scalars are truncated
MAX_LIST_ITEMS = 50
H1_RE = re.compile(r"^# (.+)$", re.MULTILINE)
SECTION_RE = re.compile(r"^## (.+)$", re.MULTILINE)


@dataclass
class Page:
    path: str  # vault-relative, posix
    frontmatter: dict
    body: str  # everything after frontmatter
    raw: str  # full file text, frontmatter included

    @property
    def title(self) -> str:
        m = H1_RE.search(self.body)
        return m.group(1).strip() if m else Path(self.path).stem

    @property
    def wikilinks(self) -> list[str]:
        """Distinct link targets (normalized stems), frontmatter + body, file order."""
        seen: dict[str, None] = {}
        for target in WIKILINK_RE.findall(self.raw):
            seen.setdefault(normalize_target(target))
        return list(seen)

    def sections(self) -> list[tuple[str, str]]:
        """(heading, text) chunks split on ## headings; the preamble has heading ''.
        These are the units that get embedded for `wiki search`."""
        parts = SECTION_RE.split(self.body)
        chunks = []
        preamble = parts[0].strip()
        if preamble:
            chunks.append(("", preamble))
        for heading, text in zip(parts[1::2], parts[2::2]):
            chunks.append((heading.strip(), text.strip()))
        return chunks or [("", "")]


def normalize_target(target: str) -> str:
    return target.strip().lower().replace(" ", "-")


def parse_page(vault: Path, rel: str) -> Page:
    raw = (vault / rel).read_text(encoding="utf-8", errors="replace")
    frontmatter: dict = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            block = raw[4:end]
            if len(block) > MAX_FRONTMATTER_BYTES:
                frontmatter = {}
            else:
                try:
                    frontmatter = yaml.safe_load(block) or {}
                except yaml.YAMLError:
                    frontmatter = _salvage_frontmatter(block)
            body = raw[end + 5:]
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return Page(path=rel, frontmatter=frontmatter, body=body, raw=raw)


def scalar(value) -> str | None:
    """A frontmatter value as a bounded string, or None if it is not a scalar.
    Lists, dicts and YAML alias trees never reach the index as text."""
    if value is None or isinstance(value, (dict, list, tuple, set)):
        return None
    return str(value)[:MAX_FIELD_CHARS]


def scalar_list(value) -> list[str]:
    """A frontmatter list of scalars, bounded in length and item size."""
    if isinstance(value, (str, int, float)):
        value = [value]
    if not isinstance(value, list):
        return []
    out = []
    for item in value[:MAX_LIST_ITEMS]:
        s = scalar(item)
        if s:
            out.append(s)
    return out


KV_RE = re.compile(r"^(\w+):\s*(.*)$")


def _salvage_frontmatter(block: str) -> dict:
    """Line-by-line fallback for malformed YAML (real vault dirt exists:
    broken quoting in `related:` lists, unquoted colons in `last_change:`).
    Recovers each key whose own line parses; skips the broken ones."""
    out: dict = {}
    for line in block.splitlines():
        m = KV_RE.match(line)
        if not m:
            continue
        key, value = m.group(1), m.group(2)
        try:
            parsed = yaml.safe_load(value)
        except yaml.YAMLError:
            continue
        out[key] = parsed
    return out


def content_pages(vault: Path) -> list[str]:
    """Vault-relative paths of all content pages (not _meta, not loose files).

    Recurses into subfolders, skipping dot-directories (.obsidian, .trash, .git).
    Symlinks, FIFOs and anything that resolves outside the vault are ignored: a
    cloned or synced vault may contain links to places it has no business reading.
    """
    out = []
    for d in CONTENT_DIRS:
        base = vault / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            rel = p.relative_to(vault)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not is_regular_file_inside(p, vault):
                continue
            out.append(rel.as_posix())
    return sorted(out)


def skipped_pages(vault: Path) -> list[str]:
    """Content .md entries that content_pages() refused: symlinks, special files,
    escapes. Reported so a user learns why a page is missing from the index."""
    out = []
    for d in CONTENT_DIRS:
        base = vault / d
        if not base.is_dir():
            continue
        for p in base.rglob("*.md"):
            rel = p.relative_to(vault)
            if any(part.startswith(".") for part in rel.parts):
                continue
            if not is_regular_file_inside(p, vault):
                out.append(rel.as_posix())
    return sorted(out)

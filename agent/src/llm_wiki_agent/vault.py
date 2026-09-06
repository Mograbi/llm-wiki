"""Vault page parsing: frontmatter, titles, wikilinks, section chunks.

The vault is the source of truth; this module only reads it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import yaml

CONTENT_DIRS = ("sources", "entities", "queries", "projects", "people")

WIKILINK_RE = re.compile(r"\[\[([^\]|#]+)(?:[#|][^\]]*)?\]\]")
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
    raw = (vault / rel).read_text(encoding="utf-8")
    frontmatter: dict = {}
    body = raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 4)
        if end != -1:
            block = raw[4:end]
            try:
                frontmatter = yaml.safe_load(block) or {}
            except yaml.YAMLError:
                frontmatter = _salvage_frontmatter(block)
            body = raw[end + 5:]
    if not isinstance(frontmatter, dict):
        frontmatter = {}
    return Page(path=rel, frontmatter=frontmatter, body=body, raw=raw)


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
            out.append(rel.as_posix())
    return sorted(out)

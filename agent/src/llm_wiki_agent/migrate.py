"""Vault migration: shard _meta/log.md into per-month files.

Parse-fully-then-write: nothing is written until the whole log parses,
so a malformed file can never leave the vault half-migrated.

A log is rarely tidy: it may not be month-ordered and may carry stale
heading remnants mid-file. Lines that continue the entry above them are
folded into that entry; leftover headings are reported as strays and
preserved in the byte-identical archive. Too many strays means the parser
does not understand the format, so it aborts instead of guessing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from .fsutil import rename_nofollow, write_text_nofollow

ENTRY_RE = re.compile(r"^(?:- )?(\d{4}-\d{2})-\d{2} ")

MAX_STRAYS = 20

SHARD_HEADER = """---
type: meta
---

# Log — {month}

"""


class LogParseError(Exception):
    """The log contains more unrecognized lines than remnants explain."""


@dataclass
class ShardResult:
    shards: list[Path] = field(default_factory=list)
    strays: list[str] = field(default_factory=list)
    archived: Path | None = None       # where log.md was moved, if it was
    no_entries: bool = False           # log.md exists but holds no dated entries


def shard_log(vault: Path) -> ShardResult:
    """Split _meta/log.md into _meta/log/YYYY-MM.md shards.

    The original is moved to _meta/log/archive-full.md byte-identical.
    Idempotent: if log.md is already gone, or holds no dated entries, it is
    left alone.
    """
    log = vault / "_meta" / "log.md"
    if log.is_symlink() or not log.exists():
        return ShardResult()

    lines = log.read_text(encoding="utf-8").splitlines()

    # Preamble = everything before the first entry line.
    months: dict[str, list[str]] = {}
    strays: list[str] = []
    in_preamble = True
    current: tuple[str, int] | None = None   # (month, index) of the entry being read
    for line in lines:
        m = ENTRY_RE.match(line)
        if m:
            in_preamble = False
            bucket = months.setdefault(m.group(1), [])
            bucket.append(line)
            current = (m.group(1), len(bucket) - 1)
        elif in_preamble or line.strip() == "":
            continue
        elif line.lstrip().startswith("#"):
            # a stale heading remnant, not part of any entry
            strays.append(line)
            if len(strays) > MAX_STRAYS:
                raise LogParseError(
                    f"more than {MAX_STRAYS} unrecognized lines — "
                    f"first: {strays[0][:80]!r}"
                )
        elif current is not None:
            # a wrapped/continuation line: keep it with the entry above it
            month, i = current
            months[month][i] += "\n" + line
        else:
            strays.append(line)
            if len(strays) > MAX_STRAYS:
                raise LogParseError(
                    f"more than {MAX_STRAYS} unrecognized lines — "
                    f"first: {strays[0][:80]!r}"
                )

    if not months:
        # nothing dated to shard; do not move the user's file
        return ShardResult(strays=strays, no_entries=True)

    logdir = vault / "_meta" / "log"
    logdir.mkdir(parents=True, exist_ok=True)
    result = ShardResult(strays=strays)
    for month, entries in sorted(months.items()):
        shard = logdir / f"{month}.md"
        write_text_nofollow(shard, SHARD_HEADER.format(month=month) + "\n\n".join(entries) + "\n")
        result.shards.append(shard)

    archive = logdir / "archive-full.md"
    if archive.exists():
        archive = logdir / f"archive-full-{len(list(logdir.glob('archive-full*')))}.md"
    rename_nofollow(log, archive)
    result.archived = archive
    return result

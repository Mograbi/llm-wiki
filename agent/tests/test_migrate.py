"""Log sharder tests — TDD anchor 1.

The real _meta/log.md is NOT month-ordered (old `- ` bullet entries sit
mid-file, newest appended at the bottom), so the safety invariant is:
  1. the original file is archived byte-identical, and
  2. every entry line lands in exactly one month shard (multiset equality).
"""

import pytest

from llm_wiki_agent.migrate import shard_log, LogParseError

PREAMBLE = """---
type: meta
---

# Log

Append-only. Newest at the top. Format: `YYYY-MM-DD — <op> — <note>`.

"""

ENTRIES = [
    # old bullet format, older month, sits FIRST in file (mirrors reality)
    "- 2026-04-14 — ingest — widget-serial-missing (acme): off-subnet lookup",
    "- 2026-04-14 — update — dhcp-fleet-crash-loop: try/finally on Stop",
    # bare format, interleaved months, not sorted
    "2026-05-20 — ingest — [[scheduler-amqp-orphan-fix-2026-05-20]] (acme, edge): long entry",
    "2026-04-30 — query — [[late-april-entry]] (acme): out-of-order month",
    "2026-05-21 — journal — Work Journal 26 / week 4: appended stuff",
    "2026-06-02 — ingest — [[june-entry]] (other): something",
]


def make_log(tmp_path, body_lines=ENTRIES, preamble=PREAMBLE):
    meta = tmp_path / "_meta"
    meta.mkdir(parents=True, exist_ok=True)
    log = meta / "log.md"
    log.write_text(preamble + "\n\n".join(body_lines) + "\n", encoding="utf-8")
    return log


def read_shard(tmp_path, month):
    return (tmp_path / "_meta" / "log" / f"{month}.md").read_text(encoding="utf-8")


def entry_lines(text):
    """Entry lines of a shard/log: date-start lines, either bare or bullet form."""
    import re
    return [l for l in text.splitlines() if re.match(r"^(- )?\d{4}-\d{2}-\d{2} ", l)]


def test_shards_created_per_month(tmp_path):
    make_log(tmp_path)
    shard_log(tmp_path)
    logdir = tmp_path / "_meta" / "log"
    assert sorted(p.name for p in logdir.glob("2026-*.md")) == [
        "2026-04.md", "2026-05.md", "2026-06.md",
    ]


def test_every_entry_in_exactly_one_shard(tmp_path):
    log = make_log(tmp_path)
    original = log.read_text(encoding="utf-8")
    shard_log(tmp_path)
    sharded = []
    for shard in (tmp_path / "_meta" / "log").glob("2026-*.md"):
        sharded += entry_lines(shard.read_text(encoding="utf-8"))
    assert sorted(sharded) == sorted(entry_lines(original))


def test_month_grouping_and_file_order_preserved(tmp_path):
    make_log(tmp_path)
    shard_log(tmp_path)
    april = entry_lines(read_shard(tmp_path, "2026-04"))
    # both bullet entries then the out-of-order 04-30 entry, in file order
    assert [l.split(" — ")[0] for l in april] == ["- 2026-04-14", "- 2026-04-14", "2026-04-30"]
    may = entry_lines(read_shard(tmp_path, "2026-05"))
    assert len(may) == 2 and may[0].startswith("2026-05-20")


def test_original_archived_byte_identical(tmp_path):
    log = make_log(tmp_path)
    original = log.read_bytes()
    shard_log(tmp_path)
    assert not log.exists()
    archive = tmp_path / "_meta" / "log" / "archive-full.md"
    assert archive.read_bytes() == original


def test_idempotent_second_run_is_noop(tmp_path):
    make_log(tmp_path)
    shard_log(tmp_path)
    before = {p: p.read_bytes() for p in (tmp_path / "_meta" / "log").iterdir()}
    shard_log(tmp_path)  # log.md gone -> must not touch anything
    after = {p: p.read_bytes() for p in (tmp_path / "_meta" / "log").iterdir()}
    assert before == after


def test_heading_remnant_is_a_stray_not_sharded_but_archived(tmp_path):
    # mirrors a real log: a stale heading remnant sits between entries
    stray = "## Older entries"
    log = make_log(tmp_path, body_lines=ENTRIES[:3] + [stray] + ENTRIES[3:])
    original = log.read_bytes()
    result = shard_log(tmp_path)
    assert result.strays == [stray]
    for shard in result.shards:
        assert stray not in shard.read_text(encoding="utf-8")
    # the archive preserves every byte
    assert (tmp_path / "_meta" / "log" / "archive-full.md").read_bytes() == original


def test_wrapped_entry_keeps_its_continuation_lines(tmp_path):
    """A multi-line entry is normal markdown; its detail lines must stay with
    the entry rather than being dropped as strays."""
    entry = "2026-07-03 — ingest — [[wrapped]] (acme): first line"
    make_log(tmp_path, body_lines=[entry, "  second line of the same entry",
                                   "  third line"])
    result = shard_log(tmp_path)
    assert result.strays == []
    text = read_shard(tmp_path, "2026-07")
    assert "second line of the same entry" in text
    assert "third line" in text


def test_many_heading_remnants_abort_without_writing(tmp_path):
    strays = [f"## remnant heading {i}" for i in range(21)]
    make_log(tmp_path, body_lines=ENTRIES[:1] + strays)
    with pytest.raises(LogParseError):
        shard_log(tmp_path)
    # nothing written, original untouched
    assert not (tmp_path / "_meta" / "log").exists()
    assert (tmp_path / "_meta" / "log.md").exists()


def test_existing_log_dir_does_not_crash(tmp_path):
    """vault-template ships _meta/log/, so the directory usually already exists."""
    (tmp_path / "_meta" / "log").mkdir(parents=True)
    (tmp_path / "_meta" / "log" / "README.md").write_text("keep me", encoding="utf-8")
    make_log(tmp_path)
    result = shard_log(tmp_path)
    assert len(result.shards) == 3
    assert (tmp_path / "_meta" / "log" / "README.md").read_text() == "keep me"


def test_log_without_dated_entries_is_left_in_place(tmp_path):
    """Do not move a file we could not understand."""
    log = make_log(tmp_path, body_lines=["just some prose", "and more prose"])
    original = log.read_bytes()
    result = shard_log(tmp_path)
    assert result.no_entries and result.shards == []
    assert log.read_bytes() == original
    assert not (tmp_path / "_meta" / "log" / "archive-full.md").exists()

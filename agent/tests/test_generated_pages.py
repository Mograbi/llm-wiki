"""The generated pages an agent reads instead of the vault: the overview index,
the per-type shards with a first line per page, and the two-week hot page."""

import datetime

from llm_wiki_agent.index import build_index, generate_hot_md, generate_index_md, open_db
from llm_wiki_agent.vault import parse_page


def test_tldr_is_the_first_prose_line_after_the_title(mini_vault):
    page = parse_page(mini_vault, "entities/relay-tunnel.md")
    assert page.tldr == "Product name is Device Relay. Fronted by [[edge-gateway]]."


def test_tldr_skips_headings_tables_comments_and_bounds_length(tmp_path):
    (tmp_path / "entities").mkdir()
    long = "word " * 80
    (tmp_path / "entities" / "x.md").write_text(
        "---\ntype: entity\n---\n\n# X\n\n<!-- note -->\n\n| a | b |\n|---|---|\n\n"
        "## Section\n\n**" + long + "**\n", encoding="utf-8")
    tldr = parse_page(tmp_path, "entities/x.md").tldr
    assert tldr.startswith("word word")
    assert "*" not in tldr
    assert len(tldr) <= 161 and tldr.endswith("…")


def test_tldr_is_stored_and_backfilled_on_an_older_index(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    db.execute("UPDATE pages SET tldr = NULL")           # an index from before the column
    db.close()
    stats = build_index(mini_vault, db_path)
    assert stats["updated"] == 0                          # nothing changed on disk
    db = open_db(db_path)
    assert db.execute("SELECT COUNT(*) FROM pages WHERE tldr IS NULL").fetchone()[0] == 0


def test_overview_is_short_and_shards_carry_the_lists(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    result = generate_index_md(mini_vault, db_path)
    overview = result.path.read_text(encoding="utf-8")
    assert "## Pages by type" in overview
    assert "[[_meta/index/entities|Entities]] — 3" in overview
    assert "`acme` — 5" in overview
    assert "[[relay-tunnel]]" not in overview             # lists live in the shards
    names = sorted(s.name for s in result.shards)
    assert names == ["entities.md", "queries.md", "sources.md"]
    entities = (mini_vault / "_meta" / "index" / "entities.md").read_text(encoding="utf-8")
    assert "- [[relay-tunnel]] — Relay tunnel — Product name is Device Relay." in entities
    assert "[acme] (1←)" in entities


def test_shard_for_a_vanished_type_is_removed(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    generate_index_md(mini_vault, db_path)
    (mini_vault / "queries" / "device-relay-auto-login.md").unlink()
    build_index(mini_vault, db_path)
    generate_index_md(mini_vault, db_path)
    assert not (mini_vault / "_meta" / "index" / "queries.md").exists()


def test_hot_page_lists_recent_pages_and_recent_log_lines(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    log = mini_vault / "_meta" / "log"
    log.mkdir()
    (log / "2026-08.md").write_text(
        "---\ntype: meta\n---\n\n# Log - 2026-08\n\n"
        "2026-08-01 - ingest - [[old]] (acme): too old\n"
        "2026-08-26 - ingest - [[build-box-access-2026-08-26]] (acme): recovered access\n"
        "  continuation line stays out\n", encoding="utf-8")
    today = datetime.date(2026, 8, 28)
    hot = generate_hot_md(mini_vault, db_path, today=today).read_text(encoding="utf-8")
    assert "last 7 days (as of 2026-08-28)" in hot
    assert "- 2026-08-26 [[build-box-access-2026-08-26]]" in hot    # ingested 08-26: in window
    assert "- 2026-08-25 [[relay-tunnel]]" in hot                   # updated 08-25: in window
    assert "- 2026-07-01 [[edge-gateway]]" not in hot                # updated 07-01: out
    assert "recovered access" in hot
    assert "too old" not in hot
    assert "continuation line" not in hot


def test_hot_page_is_bounded(mini_vault, tmp_path):
    from llm_wiki_agent import index as mod
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    log = mini_vault / "_meta" / "log"
    log.mkdir()
    lines = "".join(f"2026-08-27 - update - [[p{i}]] (acme): n{i}\n" for i in range(300))
    (log / "2026-08.md").write_text("---\ntype: meta\n---\n\n" + lines, encoding="utf-8")
    hot = generate_hot_md(mini_vault, db_path, today=datetime.date(2026, 8, 28)).read_text()
    assert hot.count("\n2026-08-27 - update") == mod.HOT_MAX_LOG_LINES
    assert "[[p299]]" in hot and "[[p0]]" not in hot                 # newest kept


def test_hot_log_lines_are_truncated(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    log = mini_vault / "_meta" / "log"
    log.mkdir()
    (log / "2026-08.md").write_text("2026-08-27 - ingest - [[p]] (acme): " + "gist " * 100 + "\n",
                                    encoding="utf-8")
    hot = generate_hot_md(mini_vault, db_path, today=datetime.date(2026, 8, 28)).read_text()
    line = [l for l in hot.splitlines() if l.startswith("2026-08-27 - ingest")][0]
    assert len(line) <= 201 and line.endswith("…")

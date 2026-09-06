"""Index builder tests.

The index.db is derived and disposable; the vault is the source of truth.
Hash-check reindex must re-parse only changed pages.
"""

from conftest import FakeEmbedder

from llm_wiki_agent.index import build_index, generate_index_md, open_db


def counts(db):
    return {t: db.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("pages", "links")}


def test_pages_rows_from_frontmatter(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    rows = {r["path"]: r for r in db.execute("SELECT * FROM pages").fetchall()}
    assert len(rows) == 5
    frp = rows["entities/frp-reverse-tunnel.md"]
    assert frp["type"] == "entity"
    assert frp["projects"] == "acme"
    assert frp["title"] == "FRP reverse tunnel"
    assert frp["updated"] == "2026-08-25"
    assert rows["queries/device-vpn-auto-login.md"]["type"] == "query"


def test_link_graph_both_directions(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    out = {r["dst"] for r in db.execute(
        "SELECT dst FROM links WHERE src = ?", ("entities/frp-reverse-tunnel.md",))}
    assert out == {"envoy-proxy", "gpu-box-access-2026-08-26", "msgs-service"}
    inbound = {r["src"] for r in db.execute("SELECT src FROM links WHERE dst = ?", ("envoy-proxy",))}
    assert inbound == {"entities/frp-reverse-tunnel.md", "entities/msgs-service.md"}


def test_incremental_reparses_only_changed(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    assert build_index(mini_vault, db_path)["updated"] == 5
    page = mini_vault / "entities/envoy-proxy.md"
    page.write_text(page.read_text().replace("TLS front", "mTLS front"), encoding="utf-8")
    assert build_index(mini_vault, db_path)["updated"] == 1
    assert build_index(mini_vault, db_path)["updated"] == 0


def test_deleted_page_rows_removed(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    (mini_vault / "queries/device-vpn-auto-login.md").unlink()
    stats = build_index(mini_vault, db_path)
    assert stats["removed"] == 1
    db = open_db(db_path)
    assert counts(db)["pages"] == 4
    assert db.execute("SELECT COUNT(*) FROM links WHERE src = ?",
                      ("queries/device-vpn-auto-login.md",)).fetchone()[0] == 0


def test_generate_index_md_groups_by_type_with_inbound_counts(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    result = generate_index_md(mini_vault, db_path)
    text = result.path.read_text(encoding="utf-8")
    assert text.startswith("---\ntype: meta\ngenerated: true")
    assert "## Entities (3)" in text
    assert "## Sources (1)" in text
    assert "## Queries (1)" in text
    # envoy-proxy is linked from two pages
    assert "- [[envoy-proxy]] — Envoy proxy [acme] (2←)" in text


def test_untyped_pages_land_under_other(mini_vault, tmp_path):
    """A page whose frontmatter has no `type:` must still be findable."""
    (mini_vault / "entities/no-frontmatter.md").write_text(
        "# Plain page\n\nNo frontmatter at all.\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    result = generate_index_md(mini_vault, db_path)
    assert result.untyped == 1
    text = result.path.read_text(encoding="utf-8")
    assert "## Other (1)" in text
    assert "- [[no-frontmatter]] — Plain page" in text


def test_nested_pages_are_indexed(mini_vault, tmp_path):
    """Subfolders are normal in Obsidian; pages inside them must not vanish."""
    nested = mini_vault / "sources" / "2026" / "nested-source.md"
    nested.parent.mkdir(parents=True)
    nested.write_text("---\ntype: source\nprojects: [acme]\n---\n\n# Nested source\n",
                      encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    text = generate_index_md(mini_vault, db_path).path.read_text(encoding="utf-8")
    assert "- [[nested-source]] — Nested source" in text


def test_dot_directories_are_skipped(mini_vault, tmp_path):
    """.obsidian and .trash live inside content folders and are not pages."""
    trash = mini_vault / "entities" / ".trash" / "deleted.md"
    trash.parent.mkdir(parents=True)
    trash.write_text("---\ntype: entity\n---\n\n# Deleted\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    text = generate_index_md(mini_vault, db_path).path.read_text(encoding="utf-8")
    assert "Deleted" not in text


def test_inbound_count_survives_spaces_in_filenames(mini_vault, tmp_path):
    """[[space page]] normalizes to space-page; the count lookup must match."""
    (mini_vault / "entities" / "space page.md").write_text(
        "---\ntype: entity\n---\n\n# Space Page\n", encoding="utf-8")
    (mini_vault / "entities" / "refers.md").write_text(
        "---\ntype: entity\n---\n\n# Refers\n\nSee [[space page]].\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    text = generate_index_md(mini_vault, db_path).path.read_text(encoding="utf-8")
    assert "- [[space page]] — Space Page (1←)" in text


def test_handwritten_index_is_archived_not_overwritten(mini_vault, tmp_path):
    """The one file the generator overwrites must never eat a human's work."""
    index = mini_vault / "_meta" / "index.md"
    index.write_text("# My hand-written index\n\nPrecious.\n", encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    result = generate_index_md(mini_vault, db_path)
    assert result.archived == mini_vault / "_meta" / "index-archive.md"
    assert "Precious." in result.archived.read_text(encoding="utf-8")
    assert "GENERATED" in index.read_text(encoding="utf-8")


def test_regenerating_a_generated_index_does_not_archive_again(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    generate_index_md(mini_vault, db_path)
    again = generate_index_md(mini_vault, db_path)
    assert again.archived is None


# -- embeddings -------------------------------------------------------------

def chunk_sections(db, page):
    return [r["section"] for r in db.execute(
        "SELECT section FROM chunks WHERE page = ? ORDER BY rowid", (page,))]


def test_sections_are_embedded_per_page(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    stats = build_index(mini_vault, db_path, FakeEmbedder())
    assert stats["embedded"] == 5 and stats["coverage"] == (5, 5)
    db = open_db(db_path)
    assert chunk_sections(db, "entities/frp-reverse-tunnel.md") == \
        ["", "Retry behavior", "Teardown timers"]
    assert chunk_sections(db, "entities/envoy-proxy.md") == [""]


def test_only_changed_pages_are_reembedded(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, FakeEmbedder())
    fake = FakeEmbedder()
    page = mini_vault / "entities/envoy-proxy.md"
    page.write_text(page.read_text().replace("TLS front", "mTLS front"), encoding="utf-8")
    assert build_index(mini_vault, db_path, fake)["embedded"] == 1
    assert "mTLS front" in fake.embedded_texts[0]
    assert build_index(mini_vault, db_path, FakeEmbedder())["embedded"] == 0


def test_no_embedder_keeps_old_chunks_but_drops_stale_ones(mini_vault, tmp_path):
    """Ollama being down must not wipe the index; it must only stop lying."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, FakeEmbedder())
    page = mini_vault / "entities/envoy-proxy.md"
    page.write_text(page.read_text() + "\nnew line\n", encoding="utf-8")
    stats = build_index(mini_vault, db_path, None)
    assert stats["embedded"] == 0
    assert stats["coverage"] == (4, 5)          # the changed page lost its chunks
    db = open_db(db_path)
    assert chunk_sections(db, "entities/envoy-proxy.md") == []
    assert chunk_sections(db, "entities/frp-reverse-tunnel.md")  # untouched page kept


def test_embedder_returning_later_fills_the_gaps(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)                     # nothing embedded
    assert build_index(mini_vault, db_path, FakeEmbedder())["coverage"] == (5, 5)


def test_changing_the_embedding_model_reembeds_everything(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, FakeEmbedder("fake:v1"))
    other = FakeEmbedder("fake:v2")
    assert build_index(mini_vault, db_path, other)["embedded"] == 5


def test_deleted_page_drops_its_chunks(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, FakeEmbedder())
    (mini_vault / "queries/device-vpn-auto-login.md").unlink()
    build_index(mini_vault, db_path, FakeEmbedder())
    db = open_db(db_path)
    assert chunk_sections(db, "queries/device-vpn-auto-login.md") == []


# -- fts + change detection -----------------------------------------------------

def test_fts_rows_follow_pages(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    assert db.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 5
    (mini_vault / "queries/device-vpn-auto-login.md").unlink()
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    assert db.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 4


def test_fts_backfills_an_index_built_before_fts_existed(mini_vault, tmp_path):
    """Upgrading from an older cache: pages exist, pages_fts is empty."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path); db.execute("DELETE FROM pages_fts"); db.commit(); db.close()
    stats = build_index(mini_vault, db_path)
    assert stats["updated"] == 0                      # content did not change
    db = open_db(db_path)
    assert db.execute("SELECT COUNT(*) FROM pages_fts").fetchone()[0] == 5


def test_unchanged_files_are_not_reread(mini_vault, tmp_path, monkeypatch):
    """The mtime+size fast path: a second sync must not read file contents."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    from pathlib import Path
    real = Path.read_bytes
    reads = []
    monkeypatch.setattr(Path, "read_bytes", lambda self: reads.append(self) or real(self))
    build_index(mini_vault, db_path)
    assert reads == []


def test_touched_but_identical_file_is_not_counted_as_updated(mini_vault, tmp_path):
    import os, time
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    page = mini_vault / "entities/envoy-proxy.md"
    future = time.time() + 100
    os.utime(page, (future, future))
    assert build_index(mini_vault, db_path)["updated"] == 0
    assert build_index(mini_vault, db_path)["updated"] == 0   # stat refreshed, stays quiet


def test_older_index_without_stat_column_is_migrated(tmp_path):
    import sqlite3
    db_path = tmp_path / "old.db"
    raw = sqlite3.connect(db_path)
    raw.executescript("CREATE TABLE pages (path TEXT PRIMARY KEY, hash TEXT NOT NULL, type TEXT,"
                      " title TEXT, projects TEXT, updated TEXT, status TEXT);")
    raw.commit(); raw.close()
    db = open_db(db_path)
    assert "stat" in {r["name"] for r in db.execute("PRAGMA table_info(pages)")}

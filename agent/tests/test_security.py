"""Hostile-vault behaviour. A vault may be cloned or synced, so it can contain
symlinks, special files, and pages written by someone else."""

import os
import time

import pytest

from llm_wiki_agent import cli
from llm_wiki_agent.embed import EmbedderError, OllamaEmbedder
from llm_wiki_agent.fsutil import UnsafePath, write_text_nofollow
from llm_wiki_agent.index import build_index, generate_index_md, open_db
from llm_wiki_agent.migrate import shard_log
from llm_wiki_agent.search import search
from llm_wiki_agent.vault import WIKILINK_RE, content_pages


def page(vault, rel, text="---\ntype: entity\nprojects: [acme]\n---\n\n# T\n\nbody\n"):
    p = vault / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")
    return p


# -- reads --------------------------------------------------------------------

def test_symlinked_page_pointing_outside_the_vault_is_not_indexed(mini_vault, tmp_path):
    secret = tmp_path / "secret.txt"
    secret.write_text("root:x:0:0:top secret\n", encoding="utf-8")
    (mini_vault / "sources" / "leak.md").symlink_to(secret)
    assert "sources/leak.md" not in content_pages(mini_vault)
    db_path = tmp_path / "index.db"
    stats = build_index(mini_vault, db_path)
    assert "sources/leak.md" in stats["skipped"]
    assert search(mini_vault, db_path, "top secret", None).hits == []
    assert "leak" not in generate_index_md(mini_vault, db_path).path.read_text(encoding="utf-8")


def test_symlinked_page_inside_the_vault_is_also_skipped(mini_vault, tmp_path):
    """Even an in-vault symlink is skipped: one page, one path, no duplicates."""
    (mini_vault / "entities" / "alias.md").symlink_to(mini_vault / "entities" / "edge-gateway.md")
    assert "entities/alias.md" not in content_pages(mini_vault)


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="no FIFOs on this platform")
def test_fifo_named_like_a_page_does_not_hang_the_index(mini_vault, tmp_path):
    os.mkfifo(mini_vault / "entities" / "pipe.md")
    assert "entities/pipe.md" not in content_pages(mini_vault)
    build_index(mini_vault, tmp_path / "index.db")   # must return, not block


# -- writes -------------------------------------------------------------------

def test_index_md_symlink_is_refused(mini_vault, tmp_path):
    victim = tmp_path / "victim.txt"; victim.write_text("keep", encoding="utf-8")
    (mini_vault / "_meta" / "index.md").symlink_to(victim)
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    with pytest.raises(UnsafePath):
        generate_index_md(mini_vault, db_path)
    assert victim.read_text(encoding="utf-8") == "keep"


def test_log_shard_symlink_is_refused(mini_vault, tmp_path):
    victim = tmp_path / "victim.txt"; victim.write_text("keep", encoding="utf-8")
    (mini_vault / "_meta" / "log").mkdir()
    (mini_vault / "_meta" / "log" / "2026-01.md").symlink_to(victim)
    (mini_vault / "_meta" / "log.md").write_text(
        "2026-01-05 — ingest — [[x]] (acme): entry\n", encoding="utf-8")
    with pytest.raises(UnsafePath):
        shard_log(mini_vault)
    assert victim.read_text(encoding="utf-8") == "keep"
    assert (mini_vault / "_meta" / "log.md").exists()      # nothing moved


def test_dangling_gitignore_symlink_is_not_written_through(mini_vault, tmp_path, monkeypatch):
    target = tmp_path / "created-by-init.txt"
    (mini_vault / ".gitignore").symlink_to(target)          # dangling
    monkeypatch.setenv("WIKI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    cli.cmd_init(mini_vault)
    assert not target.exists()


def test_write_text_nofollow_refuses_dangling_symlink(tmp_path):
    link = tmp_path / "link"; link.symlink_to(tmp_path / "nowhere")
    with pytest.raises(UnsafePath):
        write_text_nofollow(link, "x")


# -- one hostile page must not brick the vault --------------------------------

def test_list_valued_type_and_status_do_not_crash_the_index(mini_vault, tmp_path):
    page(mini_vault, "entities/evil.md",
         "---\ntype: [entity, evil]\nstatus: {a: 1}\nprojects: {not: a-list}\n---\n\n# Evil\n")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    text = generate_index_md(mini_vault, db_path).path.read_text(encoding="utf-8")
    assert "- [[evil]] — Evil" in text                    # lands under Other
    assert search(mini_vault, db_path, "evil", None).hits[0].path == "entities/evil.md"


def test_yaml_alias_amplification_is_bounded(mini_vault, tmp_path):
    bomb = "---\n" + "a: &a [x,x,x,x,x,x,x,x,x,x]\n" + "".join(
        f"{c}: &{c} [*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p},*{p}]\n"
        for p, c in zip("abcdefg", "bcdefgh")) + "projects: *h\ntype: *h\n---\n\n# Bomb\n"
    page(mini_vault, "entities/bomb.md", bomb)
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    assert db_path.stat().st_size < 2_000_000
    row = open_db(db_path).execute("SELECT projects, type FROM pages WHERE path = ?",
                                   ("entities/bomb.md",)).fetchone()
    assert row["type"] is None and len(row["projects"]) < 100


def test_wikilink_regex_is_not_quadratic():
    hostile = "[[" * 40_000
    t = time.perf_counter()
    WIKILINK_RE.findall(hostile)
    assert time.perf_counter() - t < 1.0


def test_oversized_page_is_skipped_and_reported(mini_vault, tmp_path):
    page(mini_vault, "sources/huge.md", "---\ntype: source\n---\n\n# Huge\n\n" + "x" * (2 * 1024 * 1024 + 1))
    stats = build_index(mini_vault, tmp_path / "index.db")
    assert stats["oversized"] == ["sources/huge.md"]
    assert "sources/huge.md" not in {
        r["path"] for r in open_db(tmp_path / "index.db").execute("SELECT path FROM pages")}


# -- embedder trust boundary ----------------------------------------------------

def test_remote_embedder_url_is_refused_by_default(monkeypatch):
    monkeypatch.delenv("WIKI_OLLAMA_ALLOW_REMOTE", raising=False)
    with pytest.raises(EmbedderError, match="not this machine"):
        OllamaEmbedder("http://embeddings.example.com:11434")
    OllamaEmbedder("http://localhost:11434")                     # fine
    OllamaEmbedder("http://127.0.0.1:11434")                     # fine
    monkeypatch.setenv("WIKI_OLLAMA_ALLOW_REMOTE", "1")
    OllamaEmbedder("http://embeddings.example.com:11434")        # explicit opt-in


def test_malformed_embedder_response_is_an_error_not_a_traceback(monkeypatch):
    import io, json
    class R(io.BytesIO):
        def __enter__(self): return self
        def __exit__(self, *a): pass
    monkeypatch.setattr("urllib.request.urlopen",
                        lambda *a, **kw: R(json.dumps({"embeddings": [["not", "numbers"]]}).encode()))
    with pytest.raises(EmbedderError, match="unusable"):
        OllamaEmbedder("http://localhost:11434").embed(["hello"])

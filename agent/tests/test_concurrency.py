"""Several agent sessions share one vault and one cache. The index must stay
consistent under concurrent builds, generated pages must never be seen
half-written, and `wiki commit` must serialize instead of failing on index.lock.
"""

import os
import subprocess
import threading
import time

import pytest
from conftest import FakeEmbedder

from llm_wiki_agent import cli
from llm_wiki_agent.index import build_index, generate_index_md, open_db


def test_index_opens_in_wal_mode_with_a_busy_timeout(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    db = open_db(db_path)
    assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert db.execute("PRAGMA busy_timeout").fetchone()[0] >= 1000


def test_embedding_runs_outside_the_write_transaction(mini_vault, tmp_path):
    """While one build is busy embedding, another connection must still be able
    to write: the lock is taken only for the final short transaction."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)            # schema exists, so a reader can open it

    class SlowEmbedder(FakeEmbedder):
        def embed(self, texts):
            # a concurrent writer must get in while we are "calling Ollama"
            other = open_db(db_path)
            other.execute("INSERT OR REPLACE INTO meta (key, value) VALUES ('probe', 'ok')")
            other.close()
            return super().embed(texts)

    build_index(mini_vault, db_path, SlowEmbedder())
    db = open_db(db_path)
    assert db.execute("SELECT value FROM meta WHERE key = 'probe'").fetchone()[0] == "ok"
    assert db.execute("SELECT COUNT(DISTINCT page) FROM chunks").fetchone()[0] == 5


def test_concurrent_builds_from_threads_leave_one_consistent_index(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    errors = []

    def run():
        try:
            build_index(mini_vault, db_path, FakeEmbedder())
        except Exception as e:                  # pragma: no cover - the assertion below reports it
            errors.append(e)

    threads = [threading.Thread(target=run) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    db = open_db(db_path)
    assert db.execute("SELECT COUNT(*) FROM pages").fetchone()[0] == 5
    assert db.execute("SELECT COUNT(DISTINCT page) FROM chunks").fetchone()[0] == 5


def test_generated_pages_are_written_atomically(mini_vault, tmp_path, monkeypatch):
    """No `.tmp` left behind and the target is the complete new content."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    result = generate_index_md(mini_vault, db_path)
    leftovers = list((mini_vault / "_meta").rglob("*.tmp"))
    assert leftovers == []
    assert result.path.read_text(encoding="utf-8").startswith("---\ntype: meta\ngenerated: true")
    for shard in result.shards:
        assert shard.read_text(encoding="utf-8").startswith("---\ntype: meta")


def test_index_write_refuses_a_symlinked_target(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path)
    outside = tmp_path / "outside.md"
    outside.write_text("precious", encoding="utf-8")
    (mini_vault / "_meta" / "index.md").symlink_to(outside)
    from llm_wiki_agent.fsutil import UnsafePath
    with pytest.raises(UnsafePath):
        generate_index_md(mini_vault, db_path)
    assert outside.read_text(encoding="utf-8") == "precious"


# ------------------------------------------------------------------ wiki commit

@pytest.fixture
def git_vault(mini_vault):
    subprocess.run(["git", "init", "-q"], cwd=mini_vault, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=mini_vault, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=mini_vault, check=True)
    subprocess.run(["git", "add", "-A"], cwd=mini_vault, check=True)
    subprocess.run(["git", "commit", "-qm", "base"], cwd=mini_vault, check=True)
    return mini_vault


def git_out(vault, *args):
    return subprocess.run(["git", *args], cwd=vault, capture_output=True, text=True).stdout.strip()


def test_commit_stages_only_the_given_paths(git_vault, capsys):
    a = git_vault / "entities" / "edge-gateway.md"
    b = git_vault / "entities" / "control-service.md"
    a.write_text(a.read_text() + "\nmore\n", encoding="utf-8")
    b.write_text(b.read_text() + "\nother session, half written\n", encoding="utf-8")
    assert cli.cmd_commit(git_vault, "gateway: more", ["entities/edge-gateway.md"]) == 0
    assert "committed" in capsys.readouterr().out
    committed = git_out(git_vault, "show", "--name-only", "--format=", "HEAD").split()
    assert committed == ["entities/edge-gateway.md"]
    assert "control-service.md" in git_out(git_vault, "status", "--porcelain")


def test_commit_refuses_to_run_without_paths(git_vault, capsys):
    assert cli.cmd_commit(git_vault, "msg", []) == 1
    assert "never adds everything" in capsys.readouterr().err


def test_commit_reports_nothing_to_commit(git_vault, capsys):
    assert cli.cmd_commit(git_vault, "msg", ["entities/edge-gateway.md"]) == 0
    assert "nothing to commit" in capsys.readouterr().out


def test_commit_waits_out_a_foreign_index_lock(git_vault, capsys):
    """A non-CLI git process (Obsidian git, a human) holds .git/index.lock briefly."""
    lock = git_vault / ".git" / "index.lock"
    page = git_vault / "entities" / "edge-gateway.md"
    page.write_text(page.read_text() + "\nx\n", encoding="utf-8")
    # `git add` must succeed before the lock appears, so take it after staging.
    real_git = cli.git
    def staged_then_locked(vault, *args, **kw):
        r = real_git(vault, *args, **kw)
        if args[0] == "add" and not lock.exists():
            lock.write_text("", encoding="utf-8")
            threading.Timer(0.6, lambda: lock.unlink(missing_ok=True)).start()
        return r
    cli.git = staged_then_locked
    try:
        assert cli.cmd_commit(git_vault, "msg", ["entities/edge-gateway.md"]) == 0
    finally:
        cli.git = real_git
    assert git_out(git_vault, "log", "--format=%s", "-1") == "msg"


def test_parallel_commits_from_threads_all_land(git_vault):
    pages = ["entities/edge-gateway.md", "entities/control-service.md",
             "entities/relay-tunnel.md", "sources/build-box-access-2026-08-26.md"]
    for rel in pages:
        p = git_vault / rel
        p.write_text(p.read_text() + f"\nedited {rel}\n", encoding="utf-8")
    results = []
    def run(rel):
        results.append(cli.cmd_commit(git_vault, f"commit {rel}", [rel]))
    threads = [threading.Thread(target=run, args=(rel,)) for rel in pages]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == [0, 0, 0, 0]
    assert len(git_out(git_vault, "log", "--format=%h").split()) == 5   # base + 4
    assert git_out(git_vault, "status", "--porcelain") == ""


def test_commit_records_a_rename_and_a_deletion_given_by_path(git_vault):
    subprocess.run(["git", "mv", "queries/device-relay-auto-login.md", "queries/relay-auto-login.md"],
                   cwd=git_vault, check=True)
    (git_vault / "entities" / "control-service.md").unlink()
    assert cli.cmd_commit(git_vault, "rename + delete",
                          ["queries/device-relay-auto-login.md", "queries/relay-auto-login.md",
                           "entities/control-service.md"]) == 0
    assert git_out(git_vault, "status", "--porcelain") == ""
    names = git_out(git_vault, "show", "--name-status", "--format=", "HEAD")
    assert "relay-auto-login.md" in names and "control-service.md" in names


def test_commit_with_paths_never_sweeps_in_other_changes(git_vault):
    """-A is limited to the given pathspecs: another session's new page stays out."""
    (git_vault / "entities" / "other-session.md").write_text("---\ntype: entity\n---\n# o\n", encoding="utf-8")
    page = git_vault / "entities" / "edge-gateway.md"
    page.write_text(page.read_text() + "\nmine\n", encoding="utf-8")
    assert cli.cmd_commit(git_vault, "mine", ["entities/edge-gateway.md"]) == 0
    assert git_out(git_vault, "show", "--name-only", "--format=", "HEAD").split() == ["entities/edge-gateway.md"]
    assert "other-session.md" in git_out(git_vault, "status", "--porcelain")

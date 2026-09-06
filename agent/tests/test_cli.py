"""CLI tests.

`wiki init` is the only code that touches git and moves files inside someone's
existing vault, so its failure modes are the ones worth pinning down.
"""

import subprocess

import pytest

from llm_wiki_agent import cli


@pytest.fixture
def isolated_git(monkeypatch, tmp_path):
    """No global git identity, so `git commit` fails unless the CLI sets one."""
    empty = tmp_path / "gitconfig"
    empty.write_text("", encoding="utf-8")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", str(empty))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(empty))
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_CACHE", str(tmp_path / "cache"))


def git_out(vault, *args):
    return subprocess.run(["git", *args], cwd=vault, capture_output=True,
                          text=True).stdout.strip()


def test_init_succeeds_without_a_global_git_identity(mini_vault, isolated_git, cache):
    assert cli.cmd_init(mini_vault) == 0
    assert git_out(mini_vault, "rev-parse", "--verify", "HEAD")
    assert (mini_vault / "_meta" / "index.md").exists()


def test_init_commit_touches_only_meta(mini_vault, isolated_git, cache):
    """A user's unrelated staged work must not be swept into our commit."""
    wip = mini_vault / "entities" / "wip.md"
    wip.write_text("---\ntype: entity\n---\n\n# WIP\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q"], cwd=mini_vault, check=True)
    subprocess.run(["git", "config", "user.name", "T"], cwd=mini_vault, check=True)
    subprocess.run(["git", "config", "user.email", "t@t"], cwd=mini_vault, check=True)
    subprocess.run(["git", "commit", "-qm", "base", "--allow-empty"],
                   cwd=mini_vault, check=True)
    subprocess.run(["git", "add", "entities/wip.md"], cwd=mini_vault, check=True)

    assert cli.cmd_init(mini_vault) == 0

    committed = git_out(mini_vault, "show", "--name-only", "--format=", "HEAD").split()
    assert committed, "expected a migration commit"
    assert all(path.startswith("_meta/") for path in committed), committed
    assert "entities/wip.md" not in committed


def test_init_snapshots_a_repo_that_has_no_commits(mini_vault, isolated_git, cache):
    """`.git` existing is not the same as having a safety net."""
    subprocess.run(["git", "init", "-q"], cwd=mini_vault, check=True)
    assert cli.cmd_init(mini_vault) == 0
    log = git_out(mini_vault, "log", "--format=%s")
    assert "vault: initial snapshot (pre llm-wiki)" in log


def test_init_reports_an_unparseable_log_instead_of_crashing(
    mini_vault, isolated_git, cache, capsys
):
    body = "\n".join(f"## remnant heading {i}" for i in range(30))
    (mini_vault / "_meta" / "log.md").write_text(
        "2026-01-01 — ingest — [[x]] (acme): first\n" + body, encoding="utf-8")
    assert cli.cmd_init(mini_vault) == 1
    assert "could not parse" in capsys.readouterr().err


def test_init_rejects_a_directory_that_is_not_a_vault(tmp_path, capsys):
    assert cli.cmd_init(tmp_path) == 1
    assert "does not look like a vault" in capsys.readouterr().err


def test_reindex_rejects_a_directory_that_is_not_a_vault(tmp_path, capsys):
    assert cli.cmd_reindex(tmp_path, full=False) == 1
    assert "does not look like a vault" in capsys.readouterr().err


def test_reindex_regenerates_the_index(mini_vault, cache):
    assert cli.cmd_reindex(mini_vault, full=False) == 0
    text = (mini_vault / "_meta" / "index.md").read_text(encoding="utf-8")
    assert "## Entities (3)" in text


def test_main_returns_1_on_a_failed_git_command(mini_vault, monkeypatch, cache, capsys):
    def boom(*a, **kw):
        raise subprocess.CalledProcessError(128, ["git", "commit"])
    monkeypatch.setattr(cli, "cmd_init", boom)
    monkeypatch.setenv("WIKI_VAULT", str(mini_vault))
    assert cli.main(["init"]) == 1
    assert "command failed (128)" in capsys.readouterr().err


def test_search_rejects_a_directory_that_is_not_a_vault(tmp_path, capsys):
    assert cli.cmd_search(tmp_path, "x", 8, None, False) == 1
    assert "does not look like a vault" in capsys.readouterr().err


def test_search_json_output_shape(mini_vault, cache, monkeypatch, capsys):
    import json
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    assert cli.cmd_search(mini_vault, "envoy proxy", 3, None, True) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["mode"] == "lexical"
    assert {"path", "section", "snippet", "score"} <= set(data["hits"][0])
    assert data["hits"][0]["path"] == "entities/envoy-proxy.md"


def test_search_human_output_lists_paths(mini_vault, cache, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    assert cli.cmd_search(mini_vault, "teardown", 3, None, False) == 0
    out = capsys.readouterr().out
    assert out.startswith("[lexical]")
    assert "entities/frp-reverse-tunnel.md" in out


def test_reindex_reports_embedding_status(mini_vault, cache, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    assert cli.cmd_reindex(mini_vault, full=False) == 0
    assert "semantic search: off" in capsys.readouterr().out


def test_search_skips_the_vault_scan_when_recently_synced(mini_vault, cache, monkeypatch):
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    cli.cmd_reindex(mini_vault, full=False)              # fresh sync stamp
    calls = []
    monkeypatch.setattr(cli, "build_index", lambda *a, **kw: calls.append(a))
    cli.cmd_search(mini_vault, "envoy", 3, None, True)
    assert calls == []                                    # recent: no scan
    cli.cmd_search(mini_vault, "envoy", 3, None, True, force_sync=True)
    assert len(calls) == 1                                # --sync forces it


def test_search_scans_when_the_index_is_stale_or_missing(mini_vault, cache, monkeypatch):
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    calls = []
    real = cli.build_index
    monkeypatch.setattr(cli, "build_index", lambda *a, **kw: (calls.append(a), real(*a, **kw))[1])
    cli.cmd_search(mini_vault, "envoy", 3, None, True)    # never built: must scan
    assert len(calls) == 1
    monkeypatch.setattr(cli, "seconds_since_sync", lambda p: 10_000.0)
    cli.cmd_search(mini_vault, "envoy", 3, None, True)    # stale: must scan
    assert len(calls) == 2


def test_cache_path_that_is_a_file_is_reported_not_raised(mini_vault, tmp_path, monkeypatch, capsys):
    f = tmp_path / "afile"; f.write_text("x")
    monkeypatch.setenv("WIKI_CACHE", str(f))
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    monkeypatch.setenv("WIKI_VAULT", str(mini_vault))
    assert cli.main(["reindex"]) == 1
    assert "not a directory" in capsys.readouterr().err


def test_unwritable_cache_dir_is_reported_not_raised(mini_vault, tmp_path, monkeypatch, capsys):
    import os
    ro = tmp_path / "ro"; ro.mkdir(); os.chmod(ro, 0o500)
    if os.access(ro, os.W_OK):
        import pytest; pytest.skip("running as root; cannot make an unwritable dir")
    monkeypatch.setenv("WIKI_CACHE", str(ro))
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    monkeypatch.setenv("WIKI_VAULT", str(mini_vault))
    try:
        assert cli.main(["reindex"]) == 1
        assert "not writable" in capsys.readouterr().err
    finally:
        os.chmod(ro, 0o700)

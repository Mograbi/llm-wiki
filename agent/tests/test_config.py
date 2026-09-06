"""Vault path resolution: $WIKI_VAULT, then the pointer file, then ~/wiki."""

from pathlib import Path

from llm_wiki_agent import config


def test_env_var_wins(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_VAULT", str(tmp_path))
    assert config.vault() == tmp_path.resolve()


def test_pointer_file_is_used_when_env_is_unset(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    pointer = tmp_path / "pointer"
    pointer.write_text(f"{tmp_path}\n", encoding="utf-8")
    monkeypatch.setattr(config, "POINTER", pointer)
    assert config.vault() == tmp_path.resolve()


def test_empty_pointer_falls_back_to_the_default(monkeypatch, tmp_path):
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    pointer = tmp_path / "pointer"
    pointer.write_text("\n", encoding="utf-8")
    monkeypatch.setattr(config, "POINTER", pointer)
    monkeypatch.setattr(config, "DEFAULT_VAULT", tmp_path / "fallback")
    assert config.vault() == (tmp_path / "fallback").resolve()


def test_a_relative_pointer_resolves_to_an_absolute_path(monkeypatch, tmp_path):
    """install.sh may write a relative path; the CLI must not resolve it
    against whatever directory it happens to be run from."""
    monkeypatch.delenv("WIKI_VAULT", raising=False)
    (tmp_path / "myvault").mkdir()
    pointer = tmp_path / "pointer"
    pointer.write_text("myvault\n", encoding="utf-8")
    monkeypatch.setattr(config, "POINTER", pointer)
    monkeypatch.chdir(tmp_path)
    resolved = config.vault()
    assert resolved.is_absolute()
    assert resolved == (tmp_path / "myvault").resolve()


def test_env_is_read_at_call_time_not_import_time(monkeypatch, tmp_path):
    monkeypatch.setenv("WIKI_VAULT", str(tmp_path / "one"))
    first = config.vault()
    monkeypatch.setenv("WIKI_VAULT", str(tmp_path / "two"))
    assert config.vault() != first


def test_cache_expands_a_tilde(monkeypatch):
    monkeypatch.setenv("WIKI_CACHE", "~/somewhere")
    assert config.cache() == (Path.home() / "somewhere").resolve()
    assert "~" not in str(config.cache())

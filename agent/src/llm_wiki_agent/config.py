"""Configuration: where the vault and the disposable index cache live.

Vault resolution order: $WIKI_VAULT, then ~/.config/llm-wiki/vault (a one-line
file written by install.sh), else ~/wiki. Resolved on every call so tests and
callers can change the environment after import.
"""

from __future__ import annotations

import os
from pathlib import Path

POINTER = Path("~/.config/llm-wiki/vault")
DEFAULT_VAULT = Path("~/wiki")
DEFAULT_CACHE = Path("~/.cache/llm-wiki")


def vault() -> Path:
    env = os.environ.get("WIKI_VAULT")
    if env:
        return Path(env).expanduser().resolve()
    pointer = POINTER.expanduser()
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text:
            return Path(text).expanduser().resolve()
    return DEFAULT_VAULT.expanduser().resolve()


def cache() -> Path:
    return Path(os.environ.get("WIKI_CACHE", DEFAULT_CACHE)).expanduser().resolve()


def db_path() -> Path:
    return cache() / "index.db"

"""Filesystem guards. A vault can be cloned or synced, so it can contain symlinks
that point anywhere; nothing here may follow one when reading or writing."""

from __future__ import annotations

import os
import stat
from pathlib import Path


class UnsafePath(Exception):
    """A path inside the vault is a symlink or otherwise not a plain file."""


def is_regular_file_inside(path: Path, root: Path) -> bool:
    """True for a plain (non-symlink, non-FIFO) file whose real location is under root."""
    try:
        st = path.lstat()
    except OSError:
        return False
    if stat.S_ISLNK(st.st_mode) or not stat.S_ISREG(st.st_mode):
        return False
    try:
        return path.resolve().is_relative_to(root.resolve())
    except OSError:
        return False


def write_text_nofollow(path: Path, text: str) -> None:
    """Write a file, refusing to write through a symlink (dangling or not)."""
    if path.is_symlink():
        raise UnsafePath(f"refusing to write through symlink: {path}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | getattr(os, "O_NOFOLLOW", 0)
    fd = os.open(path, flags, 0o644)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)


def rename_nofollow(src: Path, dst: Path) -> None:
    """Rename, refusing if the destination is a symlink (would replace the link, or
    on some platforms follow it)."""
    if dst.is_symlink():
        raise UnsafePath(f"refusing to rename onto symlink: {dst}")
    src.rename(dst)

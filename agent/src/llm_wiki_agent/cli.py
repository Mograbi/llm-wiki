"""wiki — vault maintenance and search CLI.

  wiki search "query"        rank pages for a query (hybrid with Ollama, full-text without)
  wiki reindex               regenerate _meta/index.md (+ embeddings when Ollama is up)
  wiki init                  one-time migration of an existing vault (git, log shards, index)
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

from . import config
from .embed import OllamaEmbedder, get_embedder
from .index import build_index, generate_index_md, seconds_since_sync
from .migrate import LogParseError, shard_log
from .search import search


def git(vault: Path, *args, check=True, quiet=False):
    return subprocess.run(
        ["git", *args], cwd=vault, check=check,
        capture_output=quiet, text=True,
    )


def is_vault(vault: Path) -> bool:
    return (vault / "_meta").is_dir()


class CliError(Exception):
    """A user-facing failure: printed as one line, exit code 1, no traceback."""


def ensure_cache() -> None:
    """The index cache must be a writable directory; say so plainly if it is not."""
    path = config.cache()
    if path.exists() and not path.is_dir():
        raise CliError(f"WIKI_CACHE {path} exists and is not a directory")
    try:
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".write-test"
        probe.touch()
        probe.unlink()
    except OSError as e:
        raise CliError(f"cache directory {path} is not writable ({e.strerror})") from e


def embedding_status(embedder, stats: dict) -> str:
    done, total = stats["coverage"]
    if embedder is not None:
        return f"semantic search: on ({embedder.identity}), {done}/{total} pages embedded"
    probe = OllamaEmbedder()
    where = f"{probe.base_url} / model {probe.model}"
    if done:
        return (f"semantic search: embedder unreachable ({where}); "
                f"{done}/{total} pages still embedded from before")
    return (f"semantic search: off — no embedder at {where}. Optional: install Ollama, "
            f"`ollama pull {probe.model}`, then `wiki reindex`")


def has_commits(vault: Path) -> bool:
    return git(vault, "rev-parse", "--verify", "HEAD",
               check=False, quiet=True).returncode == 0


def ensure_git_identity(vault: Path) -> None:
    """A machine with no global git identity cannot commit. Set a repo-local
    placeholder rather than failing the migration."""
    have = git(vault, "config", "user.email", check=False, quiet=True).returncode == 0
    if have:
        return
    git(vault, "config", "user.name", "llm-wiki")
    git(vault, "config", "user.email", "llm-wiki@localhost")
    print("git: no identity configured; set a repo-local placeholder"
          f"\n  change it with: git -C {vault} config user.name 'Your Name'")


def cmd_init(vault: Path) -> int:
    if not is_vault(vault):
        print(f"error: {vault} does not look like a vault (no _meta/)", file=sys.stderr)
        return 1

    # 1. git init + a safety snapshot before anything is moved
    if not (vault / ".git").exists():
        git(vault, "init", "-q")
        git(vault, "config", "core.autocrlf", "false")
        gi = vault / ".gitignore"
        if not gi.exists():
            gi.write_text("~$*\n*.tmp\n.obsidian/workspace*\n", encoding="utf-8")
        print("git: initialized")
    else:
        print("git: already initialized")

    if not has_commits(vault):
        # a repo with no commits has no safety net yet, even if .git existed
        ensure_git_identity(vault)
        git(vault, "add", "-A")  # first snapshot of the vault: everything, deliberately
        git(vault, "commit", "-qm", "vault: initial snapshot (pre llm-wiki)")
        print("git: initial snapshot committed")

    # 2. shard a legacy single-file log (idempotent)
    try:
        result = shard_log(vault)
    except LogParseError as e:
        print(f"error: could not parse _meta/log.md — {e}\n"
              "  Expected one entry per line starting with a date:"
              " 'YYYY-MM-DD — ingest — ...'.\n"
              "  Shard it by hand into _meta/log/YYYY-MM.md, or move it aside,"
              " then re-run.", file=sys.stderr)
        return 1
    if result.shards:
        print(f"log: sharded into {len(result.shards)} month files"
              + (f" ({len(result.strays)} stray lines left in the archive only)"
                 if result.strays else ""))
        if result.archived:
            print(f"log: original preserved at {result.archived.relative_to(vault)}")
    elif result.no_entries:
        print("log: _meta/log.md holds no dated entries — left in place")
    else:
        print("log: nothing to shard")

    # 3. build the derived index + regenerate index.md (archives a hand-written one)
    ensure_cache()
    embedder = get_embedder()
    stats = build_index(vault, config.db_path(), embedder)
    index = generate_index_md(vault, config.db_path())
    if index.archived:
        print(f"index: hand-written index.md archived to {index.archived.relative_to(vault)}")
    print(f"index: {stats['updated']} pages indexed, _meta/index.md regenerated")
    if index.untyped:
        print(f"index: {index.untyped} pages have no recognized `type:`"
              " — listed under '## Other'")
    print(embedding_status(embedder, stats))

    # 4. commit the migration, and only the migration
    ensure_git_identity(vault)
    git(vault, "add", "--", "_meta")
    done = git(vault, "commit", "-qm", "wiki init: shard log, generated index",
               "--", "_meta", check=False, quiet=True)
    print("migration committed" if done.returncode == 0 else "nothing new to commit")
    return 0


def cmd_reindex(vault: Path, full: bool) -> int:
    if not is_vault(vault):
        print(f"error: {vault} does not look like a vault (no _meta/)", file=sys.stderr)
        return 1
    ensure_cache()
    if full:
        config.db_path().unlink(missing_ok=True)
    embedder = get_embedder()
    stats = build_index(vault, config.db_path(), embedder)
    index = generate_index_md(vault, config.db_path())
    if index.archived:
        print(f"index: hand-written index.md archived to {index.archived.relative_to(vault)}")
    print(f"reindexed: {stats['updated']} changed, {stats['removed']} removed, "
          f"{stats['embedded']} embedded")
    if index.untyped:
        print(f"note: {index.untyped} pages have no recognized `type:`"
              " — listed under '## Other'")
    print(embedding_status(embedder, stats))
    return 0


SYNC_MAX_AGE = 300  # seconds; walking a vault on a network mount is the slow part of a search


def cmd_search(vault: Path, query: str, k: int, project: str | None, as_json: bool,
               force_sync: bool = False) -> int:
    if not is_vault(vault):
        print(f"error: {vault} does not look like a vault (no _meta/)", file=sys.stderr)
        return 1
    ensure_cache()
    embedder = get_embedder()
    age = seconds_since_sync(config.db_path())
    if force_sync or age is None or age > SYNC_MAX_AGE:
        build_index(vault, config.db_path(), embedder)
    result = search(vault, config.db_path(), query, embedder, k=k, project=project)
    if as_json:
        print(json.dumps({
            "mode": result.mode,
            "note": result.note,
            "coverage": {"embedded": result.coverage[0], "total": result.coverage[1]},
            "hits": [asdict(h) for h in result.hits],
        }, ensure_ascii=False, indent=2))
        return 0
    header = f"[{result.mode}]"
    if result.note:
        header += f" {result.note}"
    print(header)
    if not result.hits:
        print("no matches")
        return 0
    for h in result.hits:
        where = f"  § {h.section}" if h.section else ""
        print(f"\n{h.path}{where}  ({h.score})")
        if h.snippet:
            print(f"    {h.snippet}")
    return 0


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="wiki", description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init", help="one-time migration of an existing vault")
    p_re = sub.add_parser("reindex", help="regenerate _meta/index.md")
    p_re.add_argument("--full", action="store_true",
                      help="rebuild the derived index from scratch")
    p_se = sub.add_parser("search", help="rank pages for a query")
    p_se.add_argument("query")
    p_se.add_argument("-k", type=int, default=8, help="number of pages (default 8)")
    p_se.add_argument("--project", help="only pages tagged with this project")
    p_se.add_argument("--json", action="store_true", help="machine-readable output")
    p_se.add_argument("--sync", action="store_true",
                      help="re-scan the vault first even if it was scanned in the last 5 minutes")
    args = parser.parse_args(argv)

    vault = config.vault()
    try:
        if args.cmd == "init":
            return cmd_init(vault)
        if args.cmd == "search":
            return cmd_search(vault, args.query, args.k, args.project, args.json, args.sync)
        return cmd_reindex(vault, args.full)
    except subprocess.CalledProcessError as e:
        cmd = " ".join(str(a) for a in e.cmd)
        print(f"error: command failed ({e.returncode}): {cmd}", file=sys.stderr)
        return 1
    except (CliError, OSError, sqlite3.Error) as e:
        print(f"error: {e}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

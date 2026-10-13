#!/usr/bin/env bash
# Claude Code PostToolUse hook (matcher: Edit|Write|MultiEdit).
#
# When the edited file is a page in the llm-wiki vault: refresh the derived index
# (0.1 s, incremental), lint that one page, hand the agent any defect it just
# created, and remember the path so the Stop hook can check it was committed.
# Edits anywhere else cost one JSON parse and exit 0. Never blocks an edit.
set -u
export PATH="$HOME/.local/bin:$PATH"
input="$(mktemp "${TMPDIR:-/tmp}/llm-wiki-hook.XXXXXX")" || exit 0
trap 'rm -f "$input"' EXIT HUP INT TERM
cat >"$input" 2>/dev/null || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

LLM_WIKI_HOOK_INPUT="$input" python3 - <<'PY'
import json, os, subprocess, sys, time
from pathlib import Path

CONTENT_DIRS = ("sources", "entities", "queries", "projects", "people", "agents")
PAGE_KINDS = ("broken_link", "missing_projects")   # orphans are judged at Stop, not mid-ingest


def vault_path() -> Path | None:
    env = os.environ.get("WIKI_VAULT")
    if env:
        return Path(env).expanduser()
    pointer = Path("~/.config/llm-wiki/vault").expanduser()
    if pointer.is_file():
        text = pointer.read_text(encoding="utf-8").strip()
        if text:
            return Path(text).expanduser()
    home = Path("~/wiki").expanduser()
    return home if home.is_dir() else None


def cache_dir() -> Path:
    return Path(os.environ.get("WIKI_CACHE", "~/.cache/llm-wiki")).expanduser()


try:
    data = json.load(open(os.environ["LLM_WIKI_HOOK_INPUT"], encoding="utf-8"))
except Exception:
    sys.exit(0)
tool_input = data.get("tool_input") or {}
file_path = tool_input.get("file_path") or tool_input.get("path")
vault = vault_path()
if not file_path or vault is None or not (vault / "_meta").is_dir():
    sys.exit(0)
try:
    f = Path(file_path).expanduser().resolve()
    rel = f.relative_to(vault.resolve()).as_posix()
except (OSError, ValueError):
    sys.exit(0)
if not rel.endswith(".md") or rel.split("/")[0] not in CONTENT_DIRS:
    sys.exit(0)

# remember what this session touched (the Stop hook reads it); prune old sessions
sessions = cache_dir() / "sessions"
try:
    sessions.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for old in sessions.glob("*"):
        if now - old.stat().st_mtime > 7 * 86400:
            old.unlink(missing_ok=True)
    session = str(data.get("session_id") or "nosession").replace("/", "_")
    with open(sessions / f"{session}.touched", "a", encoding="utf-8") as fh:
        fh.write(rel + "\n")
except OSError:
    pass

def wiki(*args, timeout=60):
    return subprocess.run(["wiki", *args], capture_output=True, text=True, timeout=timeout)

try:
    wiki("reindex")
    lint = wiki("lint", "--json")
    defects = json.loads(lint.stdout or "{}").get("defects", [])
except (FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
    sys.exit(0)

mine = [d for d in defects if d.get("page") == rel and d.get("kind") in PAGE_KINDS]
if not mine:
    sys.exit(0)
lines = []
for d in mine:
    if d["kind"] == "broken_link":
        lines.append(f"- broken wikilink [[{d.get('target')}]]: no such page; link only to pages that exist")
    else:
        lines.append("- missing `projects:` frontmatter (use the canonical tags in _meta/schema.md)")
context = f"llm-wiki lint for {rel} (fix before committing):\n" + "\n".join(lines)
print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                         "additionalContext": context}}))
PY
exit 0

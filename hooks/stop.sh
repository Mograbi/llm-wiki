#!/usr/bin/env bash
# Claude Code Stop hook.
#
# If this session edited vault pages that are still uncommitted, or left a lint
# defect on them, block the stop once with the list so the agent commits (with
# `wiki commit`) or says why not. Only this session's pages are checked: other
# sessions share the working tree and their half-done work is theirs. Never
# fires twice in one session, and never when Claude is already continuing from
# a Stop hook (stop_hook_active), so it cannot loop.
set -u
export PATH="$HOME/.local/bin:$PATH"
input="$(mktemp "${TMPDIR:-/tmp}/llm-wiki-hook.XXXXXX")" || exit 0
trap 'rm -f "$input"' EXIT HUP INT TERM
cat >"$input" 2>/dev/null || exit 0
command -v python3 >/dev/null 2>&1 || exit 0

LLM_WIKI_HOOK_INPUT="$input" python3 - <<'PY'
import json, os, subprocess, sys
from pathlib import Path


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


try:
    data = json.load(open(os.environ["LLM_WIKI_HOOK_INPUT"], encoding="utf-8"))
except Exception:
    sys.exit(0)
if data.get("stop_hook_active"):
    sys.exit(0)
session = str(data.get("session_id") or "nosession").replace("/", "_")
sessions = Path(os.environ.get("WIKI_CACHE", "~/.cache/llm-wiki")).expanduser() / "sessions"
touched_file = sessions / f"{session}.touched"
warned = sessions / f"{session}.stop-warned"
if not touched_file.is_file() or warned.exists():
    sys.exit(0)
vault = vault_path()
if vault is None or not (vault / ".git").exists():
    sys.exit(0)
rels = sorted({line.strip() for line in touched_file.read_text(encoding="utf-8").splitlines()
               if line.strip() and (vault / line.strip()).exists()})
if not rels:
    sys.exit(0)

try:
    status = subprocess.run(["git", "status", "--porcelain", "--", *rels], cwd=vault,
                            capture_output=True, text=True, timeout=30).stdout
except (OSError, subprocess.TimeoutExpired):
    sys.exit(0)
uncommitted = sorted(line[3:].strip() for line in status.splitlines() if line.strip())

defects = []
try:
    lint = subprocess.run(["wiki", "lint", "--json"], capture_output=True, text=True, timeout=60)
    defects = [d for d in json.loads(lint.stdout or "{}").get("defects", []) if d.get("page") in rels]
except (OSError, subprocess.TimeoutExpired, json.JSONDecodeError):
    pass

if not uncommitted and not defects:
    sys.exit(0)
try:
    warned.touch()
except OSError:
    pass
parts = []
if uncommitted:
    quoted = " ".join(f"'{p}'" for p in uncommitted)
    parts.append("vault pages you edited this session are not committed:\n  "
                 + "\n  ".join(uncommitted)
                 + f"\nCommit them with: wiki commit -m '<what changed>' -- {quoted}"
                 " (explicit paths only; other sessions share this tree).")
if defects:
    parts.append("lint defects on pages you edited:\n  " + "\n  ".join(
        f"{d['kind']}: {d['page']}" + (f" -> [[{d['target']}]]" if d.get("target") else "")
        for d in defects) + "\nFix them if they are yours, or say why they stand.")
reason = "llm-wiki: " + "\n\n".join(parts) + "\n\nThen finish. If the user asked you to leave the vault uncommitted, say so and stop."
print(json.dumps({"decision": "block", "reason": reason}))
PY
exit 0

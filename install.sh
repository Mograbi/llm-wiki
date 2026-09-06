#!/usr/bin/env bash
# llm-wiki installer: vault + agent skill + optional CLI.
#   ./install.sh [VAULT_PATH]     (default: ~/wiki)
set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VAULT="${1:-$HOME/wiki}"
VAULT="${VAULT/#\~/$HOME}"
CONFIG_DIR="$HOME/.config/llm-wiki"
BIN_DIR="$HOME/.local/bin"

say()  { printf '\033[1m%s\033[0m\n' "$*"; }
warn() { printf '\033[1;33m%s\033[0m\n' "$*"; }

# 1. vault ------------------------------------------------------------------
FRESH_VAULT=0
if [ -d "$VAULT/_meta" ]; then
  say "vault: $VAULT already exists, leaving its contents alone"
else
  say "vault: creating $VAULT from vault-template/"
  mkdir -p "$VAULT"
  cp -R "$REPO/vault-template/." "$VAULT/"
  FRESH_VAULT=1
fi

# Store an absolute path: the CLI must not resolve it against its own cwd.
VAULT="$(cd "$VAULT" && pwd)"
mkdir -p "$CONFIG_DIR"
printf '%s\n' "$VAULT" > "$CONFIG_DIR/vault"
say "config: vault path written to $CONFIG_DIR/vault"

# git is optional here; the vault is still a usable folder of markdown without it.
if [ "$FRESH_VAULT" = 1 ]; then
  if command -v git >/dev/null 2>&1; then
    if [ ! -d "$VAULT/.git" ]; then
      git -C "$VAULT" init -q
      git -C "$VAULT" config core.autocrlf false
      if [ -z "$(git -C "$VAULT" config user.email || true)" ]; then
        git -C "$VAULT" config user.name "llm-wiki"
        git -C "$VAULT" config user.email "llm-wiki@localhost"
        warn "git: no identity configured; set a vault-local placeholder"
        warn "     change it with: git -C \"$VAULT\" config user.name 'Your Name'"
      fi
      git -C "$VAULT" add -A   # first snapshot of a fresh template: everything, deliberately
      git -C "$VAULT" commit -qm "vault: initial template" || true
      say "git: vault initialized"
    fi
  else
    warn "git: not found; the vault is not under version control (install git, then: git -C \"$VAULT\" init)"
  fi
fi

# 2. CLI (optional) ---------------------------------------------------------
CLI_OK=0
if command -v python3 >/dev/null 2>&1 \
   && python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' 2>/dev/null; then
  say "cli: installing the wiki CLI into agent/.venv"
  if ( cd "$REPO/agent" && python3 -m venv .venv && .venv/bin/pip install -q -e . ); then
    mkdir -p "$BIN_DIR"
    ln -sf "$REPO/agent/.venv/bin/wiki" "$BIN_DIR/wiki"
    CLI_OK=1
    say "cli: linked $BIN_DIR/wiki"
    case ":$PATH:" in
      *":$BIN_DIR:"*) ;;
      *) warn "warning: $BIN_DIR is not on your PATH — add this to your shell profile:"
         warn "         export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
    esac
  else
    warn "cli: install failed — skipping it (the skill still works without the CLI)"
  fi
else
  found="$(python3 -V 2>&1 || echo none)"
  warn "cli: need python3 >= 3.11 (found: $found) — skipping it (the skill still works without the CLI)"
fi

# 3. first index ------------------------------------------------------------
if [ "$CLI_OK" = 1 ]; then
  if command -v ollama >/dev/null 2>&1; then
    say "search: ollama found — semantic search turns on once 'ollama pull nomic-embed-text' has run and 'wiki reindex' sees it"
  else
    say "search: lexical mode (optional upgrade: install Ollama, 'ollama pull nomic-embed-text', then 'wiki reindex')"
  fi
  if [ "$FRESH_VAULT" = 1 ]; then
    if "$BIN_DIR/wiki" reindex >/dev/null; then
      say "index: _meta/index.md generated"
      if command -v git >/dev/null 2>&1 && [ -d "$VAULT/.git" ]; then
        git -C "$VAULT" add -- _meta/index.md
        git -C "$VAULT" commit -qm "vault: generated index" -- _meta/index.md || true
      fi
    else
      warn "index: build failed — run 'wiki reindex' to see the error"
    fi
  else
    say "index: existing vault — run 'wiki init' to migrate it (shards the log, generates the index)"
  fi
fi

# 4. Claude Code skill ------------------------------------------------------
mkdir -p "$HOME/.claude/skills"
ln -sfn "$REPO/skill/llm-wiki" "$HOME/.claude/skills/llm-wiki"
say "claude code: skill linked at ~/.claude/skills/llm-wiki (invoke with /llm-wiki)"

# 5. Cursor -----------------------------------------------------------------
say "cursor: copy $REPO/cursor/llm-wiki.mdc into <your-project>/.cursor/rules/ (see docs/cursor.md)"

cat <<MSG

Done. Keep this checkout where it is — the skill and CLI are symlinked into it.

Next:
  1. Edit $VAULT/_meta/schema.md: replace the 'example' project tag with your
     first project, and set repo_root under Repositories.
  2. Open your agent and say: "ingest this" with a link, file, or pasted conversation.
MSG

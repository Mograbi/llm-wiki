#!/usr/bin/env bash
# Inside the container: one librarian run.
#   clone or fetch the vault -> fresh branch agent/<name>-<date> off the default branch
#   -> wiki reindex + lint -> claude -p with prompt.md -> push the branch if it committed.
# Required env: VAULT_REPO (ssh URL) and either CLAUDE_CODE_OAUTH_TOKEN or ANTHROPIC_API_KEY.
# The deploy key is mounted read-only at /run/secrets/deploy_key.
set -euo pipefail

: "${VAULT_REPO:?set VAULT_REPO to the vault git ssh url}"
: "${LIBRARIAN_NAME:=librarian}"
: "${LIBRARIAN_MODEL:=claude-sonnet-5-5}"
: "${LIBRARIAN_MAX_TURNS:=80}"
: "${LIBRARIAN_DRY_RUN:=0}"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
KEY=/run/secrets/deploy_key
[ -r "$KEY" ] || { echo "error: deploy key not mounted at $KEY" >&2; exit 1; }
export GIT_SSH_COMMAND="ssh -i $KEY -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new -o UserKnownHostsFile=$HOME/.ssh/known_hosts"

log() { printf '%s %s\n' "$(date -u +%FT%TZ)" "$*"; }

if [ ! -d /vault/.git ]; then
  log "cloning $VAULT_REPO"
  git clone -q "$VAULT_REPO" /vault
else
  log "fetching"
  git -C /vault fetch -q --prune origin
fi
cd /vault
default="$(git symbolic-ref -q --short refs/remotes/origin/HEAD 2>/dev/null || true)"
default="${default#origin/}"
[ -n "$default" ] || default="$(git remote show origin | sed -n 's/.*HEAD branch: //p')"
git config user.name "$LIBRARIAN_NAME"
git config user.email "${LIBRARIAN_NAME}@vault"
git config core.autocrlf false

date="$(date -u +%F)"
branch="agent/${LIBRARIAN_NAME}-${date}"
# this clone is disposable: drop anything a previous run left (generated pages that
# used to be tracked, an aborted edit), then start from the remote's default branch
git reset -q --hard
git checkout -q --detach "origin/$default"
git checkout -q -B "$branch"
log "on $branch from origin/$default ($(git rev-parse --short HEAD))"

wiki reindex >/dev/null
wiki lint --json > /tmp/lint-before.json || true
log "lint before: $(python3 -c 'import json;d=json.load(open("/tmp/lint-before.json"));print(d["counts"])')"

prompt="$(sed -e "s|{{NAME}}|$LIBRARIAN_NAME|g" -e "s|{{DATE}}|$date|g" -e "s|{{BRANCH}}|$branch|g" "$HERE/prompt.md")"
if [ "$LIBRARIAN_DRY_RUN" = 1 ]; then
  log "dry run: not calling claude"; printf '%s\n' "$prompt" | head -20; exit 0
fi

log "claude -p ($LIBRARIAN_MODEL, max $LIBRARIAN_MAX_TURNS turns)"
set +e
claude -p --model "$LIBRARIAN_MODEL" --max-turns "$LIBRARIAN_MAX_TURNS" \
       --dangerously-skip-permissions --output-format text "$prompt" > /tmp/report.md
rc=$?
set -e
log "claude exited $rc"
sed 's/^/  | /' /tmp/report.md | tail -60

ahead="$(git rev-list --count "origin/$default..HEAD")"
if [ "$ahead" -gt 0 ]; then
  git push -q -u origin "$branch"
  log "pushed $branch ($ahead commits). Merge it from your machine:"
  log "  git -C <vault> fetch && git -C <vault> merge --no-ff origin/$branch && wiki reindex"
else
  log "no commits on $branch; nothing pushed"
fi
# leave the work tree clean for the next run
git checkout -q --detach "origin/$default"
git branch -q -D "$branch" 2>/dev/null || true
exit "$rc"

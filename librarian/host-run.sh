#!/usr/bin/env bash
# On the host that runs the librarian (cron entry). Everything else happens in the container.
# Layout next to this script:  env  (VAULT_REPO=..., CLAUDE_CODE_OAUTH_TOKEN=... or ANTHROPIC_API_KEY=...,
#                                    optional LIBRARIAN_MODEL / LIBRARIAN_MAX_TURNS / LIBRARIAN_DRY_RUN)
#                               secrets/deploy_key   (read-only deploy key with write access to the vault repo)
#                               vault/  cache/  logs/  (created here)
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
IMAGE="${LIBRARIAN_IMAGE:-llm-wiki-librarian:latest}"
mkdir -p "$HERE/logs" "$HERE/vault" "$HERE/cache"
[ -f "$HERE/env" ] || { echo "error: $HERE/env missing (VAULT_REPO and a Claude token)" >&2; exit 1; }
[ -f "$HERE/secrets/deploy_key" ] || { echo "error: $HERE/secrets/deploy_key missing" >&2; exit 1; }
exec docker run --rm --name llm-wiki-librarian \
  --env-file "$HERE/env" -e LIBRARIAN_DRY_RUN \
  -v "$HERE/vault:/vault" \
  -v "$HERE/cache:/home/librarian/.cache" \
  -v "$HERE/secrets/deploy_key:/run/secrets/deploy_key:ro" \
  "$IMAGE" "$@" >> "$HERE/logs/$(date -u +%F).log" 2>&1

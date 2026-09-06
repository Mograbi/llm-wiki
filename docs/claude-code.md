# Using llm-wiki with Claude Code

## Install

`./install.sh` symlinks `skill/llm-wiki` into `~/.claude/skills/llm-wiki`. Claude Code picks up skills from that directory automatically; the skill appears as `/llm-wiki` and is also invoked implicitly when you ask to ingest, save, look up, log, or lint knowledge.

Manual install:

```bash
ln -s /path/to/llm-wiki/skill/llm-wiki ~/.claude/skills/llm-wiki
```

To scope it to one project instead of your whole account, put it under `<project>/.claude/skills/llm-wiki` instead.

## Vault location

The skill looks for the vault in this order: `$WIKI_VAULT`, then `~/.config/llm-wiki/vault`, then `~/wiki`. `install.sh` writes the second one. Set the first in your shell profile if you keep several vaults.

## Daily use

| You say | The agent does |
|---|---|
| `/llm-wiki ingest <link or pasted text>` | Ingest workflow, one commit |
| "what do we know about X?" | Query workflow: `wiki search`, read, walk links, cited answer, maybe a saved query |
| "log today" | Commit scan, diff against the log, ingest the gap |
| "lint the vault" | Read-only punch list |
| "do you have anything to save?" (new session) | Killed-session recovery |

## Memory vs vault

Claude Code has its own auto-memory for facts about you: preferences, how you like to work. Keep that small. Domain knowledge goes in the vault. The skill states this split so the agent does not file a vendor quirk in memory or your code-style preference in the vault.

## Tips

- Say "ingest" explicitly when you paste something. The agent otherwise treats pasted text as a question.
- Ask for a lint about once a month, or after a big ingest burst.
- The vault is a git repo. `git log` in the vault is a second, richer changelog next to `_meta/log/`.
- If Obsidian or a sync client is open on the vault, in-place edits to `_meta/*` can fail with "file modified since read". The skill appends to log shards with a heredoc for that reason; if it happens on another file, ask the agent to retry.

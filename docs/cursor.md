# Using llm-wiki with Cursor

Cursor uses project rules instead of skills. The rule in `cursor/llm-wiki.mdc` is the skill condensed into Cursor's `.mdc` format.

## Install

Copy the rule into the project where you want the vault available:

```bash
mkdir -p .cursor/rules
cp /path/to/llm-wiki/cursor/llm-wiki.mdc .cursor/rules/
```

The rule has `alwaysApply: false` and a description, so Cursor attaches it when your request is about ingesting, saving, looking up, logging, or linting knowledge. Mention "vault" or "wiki" in your prompt to make attachment reliable, or reference it explicitly with `@llm-wiki`.

For a user-wide rule, paste the body into Cursor Settings > Rules for AI. Keep the vault path line correct for your machine.

## Vault location

Same resolution as the CLI: `$WIKI_VAULT`, then `~/.config/llm-wiki/vault`, then `~/wiki`. Cursor's agent runs shell commands in your environment, so an exported `WIKI_VAULT` in your shell profile is the simplest option.

## Daily use

The same phrases work: "ingest this", "what do we know about X", "log today", "lint the vault". Cursor's agent mode is required for the file writes and git commits. In ask mode the rule still helps the model answer from the vault, but nothing is saved.

## The `wiki` CLI

If installed, the rule tells the agent to run `wiki search` for questions and `wiki reindex` after ingests. Make sure `~/.local/bin` is on the PATH Cursor's terminal inherits.

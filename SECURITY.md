# Security

## Threat model

`llm-wiki` runs on your machine, against a folder of markdown you own. It has no server and no listener. The parts that matter:

- **The vault may contain untrusted text.** Ingested web pages, pasted chats, files other people wrote, and anything a cloned or synced vault brings along, including symlinks. The CLI treats every page as data: symlinks and special files are skipped, writes never follow symlinks, frontmatter values are bounded, pages over 2 MB are skipped, and the wikilink parser is bounded so one hostile page cannot stall indexing. The skill tells the agent the same thing: vault content is never an instruction.
- **`WIKI_OLLAMA_URL` is a trust boundary.** Every section of every page is sent to that endpoint for embedding. By default only loopback hosts are accepted; set `WIKI_OLLAMA_ALLOW_REMOTE=1` to send vault text off-machine knowingly.
- **The installer touches your home directory.** It symlinks into the checkout (`~/.local/bin/wiki`, `~/.claude/skills/llm-wiki`), writes `~/.config/llm-wiki/vault`, and git-inits only a directory it created itself. It refuses to turn a non-empty existing directory into a vault.
- **`wiki init` commits.** Its first commit snapshots an existing vault wholesale, on purpose, so the migration has an undo. Its second commit touches `_meta/` only.

Out of scope: an attacker who already controls your shell environment or your home directory.

## Reporting

Open a private security advisory on GitHub (Security tab, "Report a vulnerability"), or an issue if it is not sensitive. Please include a reproduction. Fixes land as ordinary commits with a test.

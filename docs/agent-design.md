# CLI design (`wiki`)

A deliberately small tool. The agent you already use (Claude Code, Cursor) does the reading, synthesis, and writing; the CLI does the three things an LLM should not do by hand: rank pages for a query, keep the generated index current, and migrate an existing vault into this layout.

## Pieces

- **`vault.py`**: page parsing. Frontmatter (YAML), title, `[[wikilinks]]` from both frontmatter and body, `##`-section chunks for embedding. Recurses into subfolders; skips dot-directories.
- **`search.py`**: `wiki search`. Two rankers, fused by reciprocal rank when both are available. Lexical is SQLite FTS5 with BM25 (Porter stemming, title weighted); semantic is cosine over section chunks, best section per page. Always reports which mode ran and whether coverage is partial.
- **`embed.py`**: optional local embeddings through Ollama's HTTP API. `probe()` checks both that the daemon answers and that the model is pulled; anything else means "no embedder" and the rest of the tool carries on.
- **`index.py`**: a sqlite index of pages (path, hash, mtime+size, type, title, projects, updated, status), the wikilink graph, an FTS5 full-text table, and section chunks with float32 embeddings. Hash-keyed incremental rebuild: only changed pages are re-parsed, deleted pages have their rows removed. Regenerates `_meta/index.md` grouped by page type with inbound-link counts as a salience hint; pages without a recognized `type:` land under `## Other` rather than vanishing. A hand-written `index.md` is moved to `index-archive.md` before the first generation, never overwritten.
- **`migrate.py`**: shards a legacy single `_meta/log.md` into monthly `_meta/log/YYYY-MM.md` files. Parse fully, then write; the original is archived byte-identical. Continuation lines stay with the entry above them; a log with no dated entries is left in place rather than moved.
- **`cli.py`**: `search`, `reindex`, `init`.
- **`config.py`**: vault and cache path resolution, evaluated on each call so the environment can change after import.

## Decisions worth knowing

**The index is disposable.** Delete `~/.cache/llm-wiki/index.db` at any time; the next command rebuilds it. Nothing the vault depends on lives outside the vault.

**`_meta/index.md` is generated, never edited.** Sync clients (OneDrive, Dropbox, Obsidian Sync) touch `_meta/` mid-session and in-place edits fail with "modified since read". A generated file has no such race, and the agent never has to maintain a catalog by hand.

**Log shards are append-only.** The same sync race is why the skill appends to `_meta/log/YYYY-MM.md` with a heredoc rather than rewriting the file. `migrate.py` exists to get an older single-file log into that shape once.

**No model calls in the CLI.** An earlier version carried its own retrieval agent with model backends. It was removed: the coding agent already reads and reasons better than a second model in the loop, and that second model meant a second set of credentials, prompts, and failure modes to explain. What stayed is the part a model cannot do well by hand: ranking a few hundred pages for a query. `wiki search` returns paths, sections, and snippets. The agent reads them. Nothing in the CLI can hallucinate a citation.

**Embeddings are optional and local by default.** Ollama with `nomic-embed-text` is the only supported embedder and its absence is not an error; search falls back to lexical ranking and says so on its first line. Because every page section is posted to `WIKI_OLLAMA_URL`, that variable is a trust boundary: non-loopback hosts are refused unless `WIKI_OLLAMA_ALLOW_REMOTE=1` is set.

**A vault is untrusted input.** It may be cloned or synced, so it can contain symlinks to anywhere, FIFOs, pages with list-valued or alias-expanded frontmatter, and megabytes of `[[`. The reader skips anything that is not a plain file resolving inside the vault, writes never follow a symlink, stored frontmatter values are bounded, the wikilink regex is bounded and newline-free, and pages over 2 MB are skipped with a message. The design goal is that one hostile page can cost you one page, never the index.

**Coverage may be partial, never silently.** If the embedder is down during a reindex, unchanged pages keep their chunks and changed pages lose theirs, because stale embeddings would rank confidently on text that no longer exists. Search reports "N of M pages embedded" whenever the two differ. Changing the embedding model re-embeds everything, since vectors from different models do not compare.

**Brute-force cosine.** At a few thousand section chunks a linear scan over float32 blobs in sqlite takes milliseconds and needs no vector database. Revisit past roughly fifty thousand chunks.

**Hybrid by reciprocal rank, plus a top-1 guarantee.** Cosine similarities and BM25 scores are not on comparable scales, so the two lists are fused by rank: each page scores the sum of 1/(60 + rank) over the lists it appears in. Plain RRF has a known failure: a page only one ranker finds, such as an exact rare token that only BM25 sees, is outscored by pages both lists rank in the middle. So each ranker's first choice is promoted into the top three. Measured on a 465-page vault against fifteen questions with known answers: semantic alone put the answer in the top eight for 14 of 15 with a mean reciprocal rank of 0.70; fused with the guarantee, 15 of 15 and 0.73. Small on paper, but the one it recovers is exactly the "fact buried in a long section" case. A parameter sweep showed the RRF constant barely matters and a candidate depth of 20 beats 40.

**Two-stage change detection, and a throttle.** A file whose mtime and size are unchanged is skipped without being read; otherwise the content hash decides. That removes the reads, but on a sync-client mount the directory walk and the stat calls themselves are slow: 12 seconds fell only to 9 on one such vault. So `wiki search` re-scans at most once every five minutes, `wiki reindex` always scans, and `wiki search --sync` forces it. The skill runs reindex after every ingest, so new pages are searchable immediately anyway.

**Explicit-path commits.** `wiki init` makes exactly two commits: the initial snapshot of a pre-existing vault (everything, deliberately) and the migration, committed with an explicit `_meta` pathspec so a user's unrelated staged work is never swept in. Everything after that is the agent's job, and the skill forbids `git add -A` because other sessions and sync clients edit the same tree.

**No global git identity is not an error.** A fresh machine cannot commit. Both the installer and `wiki init` set a repo-local placeholder identity and say so, rather than failing halfway through a migration.

## Extending

- A new page type: add it to `TYPE_ORDER` in `index.py` and to `_meta/schema.md`.
- A new frontmatter field in the index: add the column to `SCHEMA` and the insert in `build_index`; bump nothing else, the index is disposable.
- A different embedder: implement `identity` and `embed(texts) -> vectors`, return it from `embed.get_embedder`. Identity is what triggers a full re-embed when it changes.
- Vault conventions live in `_meta/schema.md` and the skill, not in Python. The CLI only assumes the folder names and the frontmatter keys it indexes.

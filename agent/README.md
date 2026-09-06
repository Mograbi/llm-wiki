# wiki CLI (agent/)

A small local CLI for an LLM Wiki vault. It finds pages, keeps `_meta/index.md` generated, and migrates a pre-existing vault into the layout the skill expects. It never calls a language model: synthesis is your coding agent's job.

```
wiki search "question" [-k 8] [--project TAG] [--json] [--sync]
                           # rank pages for a query: path, best section, snippet, score
wiki reindex [--full]      # regenerate _meta/index.md (grouped by type, inbound-link counts);
                           # refresh section embeddings when Ollama is reachable
wiki init                  # one-time migration of an existing vault: git init, shard a legacy
                           # single-file log into _meta/log/YYYY-MM.md, archive a hand-written
                           # index, generate the new one
```

## Search modes

| Mode | When | How |
|---|---|---|
| `hybrid` | Ollama is reachable and pages have been embedded by a `reindex` | reciprocal-rank fusion of the two rankers below; each ranker's first choice is guaranteed a top-3 slot |
| `semantic` | embeddings available but the query is all stopwords | cosine over `##`-section chunks (`nomic-embed-text`), best section per page |
| `lexical` | no embedder, or nothing embedded yet | SQLite FTS5 with BM25, Porter stemming, title weighted 5x; falls back to disk parsing only if the sqlite build lacks FTS5 |

The first output line names the mode and warns when only part of the vault is embedded. Measured on a 465-page vault with fifteen questions: hybrid put the right page in the top 8 for 15 of 15 (top 3 for 14) with a mean reciprocal rank of 0.73; grep-by-hand with well-chosen keywords managed 14 and 11 at 0.67, and returned a median of 123 candidate files per question with no ranking or snippets. Lexical alone answers in about 60 ms. Coverage can be partial on purpose: if Ollama is down during a reindex, unchanged pages keep their embeddings and changed pages drop theirs until the next reindex.

## Setup

```
python3 -m venv .venv && .venv/bin/pip install -e .[dev]
ln -s $PWD/.venv/bin/wiki ~/.local/bin/wiki
```

Optional semantic search: install [Ollama](https://ollama.com/download), then `ollama pull nomic-embed-text` and `wiki reindex`.

| Variable | Default | Meaning |
|---|---|---|
| `WIKI_VAULT` | pointer file, else `~/wiki` | vault path (`~/.config/llm-wiki/vault` is written by `install.sh`) |
| `WIKI_CACHE` | `~/.cache/llm-wiki` | disposable sqlite index; delete any time. Change detection is mtime+size first, content hash second |

`wiki search` re-scans the vault for changes at most once every five minutes; pass `--sync` to force it. `wiki reindex` always scans.
| `WIKI_OLLAMA_URL` | `http://localhost:11434` | embedder endpoint; loopback only unless `WIKI_OLLAMA_ALLOW_REMOTE=1` |
| `WIKI_EMBED_MODEL` | `nomic-embed-text` | embedding model; changing it re-embeds everything |
| `WIKI_NO_EMBED` | unset | set to `1` to never try the embedder |

No API keys. The only network call is to the embedder at `WIKI_OLLAMA_URL`, which must be a loopback address unless you set `WIKI_OLLAMA_ALLOW_REMOTE=1`, because every page section is sent there. One dependency (`pyyaml`). See [SECURITY.md](../SECURITY.md) for the threat model.

## Tests

```
.venv/bin/pytest
```

Design notes: [../docs/agent-design.md](../docs/agent-design.md).

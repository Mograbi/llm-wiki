---
type: source
source_type: gist
url: https://gist.github.com/karpathy/442a6bf555914893e9891c11519de94f
author: Andrej Karpathy
ingested: 2026-01-01
projects: [example]
entities: ["[[llm-wiki-pattern]]"]
related: []
status: summarized
tags: [knowledge-management, meta]
---

# Karpathy - LLM Wiki

The gist that describes the pattern this vault implements. Summary, not verbatim; revisit the URL if exact wording matters.

## Core idea

Instead of retrieving raw documents on every question (classic RAG), the LLM **incrementally builds and maintains a persistent wiki**: a structured, interlinked set of markdown files that sits between the human and the source material.

## Three layers

1. **Raw sources** - immutable inputs the LLM reads but never modifies.
2. **The wiki** - LLM-written markdown: summaries, concept pages, cross-references.
3. **The schema** - the file that defines structure and workflows.

## Three operations

- **Ingest** - process a new source, update the pages it touches, maintain cross-links.
- **Query** - search the wiki, synthesize an answer with citations, file valuable answers back.
- **Lint** - health-check for contradictions, stale claims, orphans, missing links.

## Why it works

> The tedious part of maintaining a knowledge base is not the reading or the thinking; it is the bookkeeping.

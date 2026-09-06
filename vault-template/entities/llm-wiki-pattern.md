---
type: entity
projects: [example]
related: []
sources: ["[[karpathy-llm-wiki]]"]
status: stable
updated: 2026-01-01
tags: [knowledge-management, meta]
---

# LLM Wiki pattern

A knowledge-management pattern in which an LLM incrementally builds and maintains a persistent, interlinked markdown knowledge base, layered between a human curator and raw source material. Originated by Karpathy; see [[karpathy-llm-wiki]].

## Key insight

The bottleneck in a knowledge base is bookkeeping, not thinking. An agent makes the bookkeeping nearly free, so the pattern stays viable without human-linear maintenance cost.

## How this vault implements it

- Raw sources: the URLs, documents, and conversations referenced from `sources/`.
- Wiki: this folder.
- Schema: `_meta/schema.md`, plus the `llm-wiki` agent skill.
- Retrieval: your coding agent (grep, read, wikilink-walking), guided by the `llm-wiki` skill. The `wiki` CLI only regenerates `_meta/index.md`.

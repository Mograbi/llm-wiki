# Workflows

The agent runs five workflows over the vault. Three are Karpathy's originals; two exist because agent sessions die and days end.

## Ingest

Trigger: the user shares a source. A paper, a gist, a Slack thread, a vendor PDF, a post-mortem, a transcript, a design doc.

1. Read `_meta/schema.md`.
2. Write `sources/<slug>.md`: full frontmatter, then a clean summary. Add an ISO date suffix to the slug only if the source is time-anchored (an incident, a single day's investigation). Evergreen references (an API document) get no date.
3. For every concept the source materially touches, update or create `entities/<concept>.md`. Search for near-duplicates first. Bump `updated:` and append the source to `sources:`.
4. Cross-link both ways: the source lists its entities, each entity lists the source.
5. Sibling sources ingested in the same turn (same PR, branch, or root cause) point at each other through `related:`.
6. Link project-scoped sources from the project hub.
7. Append one line to `_meta/log/YYYY-MM.md`.
8. Commit with explicit paths.

What a good source page looks like: the reader can tell in ten seconds what the source was, who produced it, when, what it claims, and which concept pages it changed. Verbatim quotes are marked as such. Uncertainty is stated, not smoothed over.

## Query

Trigger: the user asks something the vault might know.

1. The agent runs `wiki search` for candidate pages when the CLI is installed, otherwise starts from the generated index and the project hub and greps for the key terms. It reads the best two to four pages and walks their inbound and outbound links. Entities first; sources back them up.
2. The answer cites `[[pages]]` that exist. Never a page name the agent did not read.
3. If the answer is non-trivial, grounded in several pages, and likely to recur, it is saved to `queries/<slug>.md` and logged.
4. If the vault has nothing useful, the answer says so. A short, clearly marked paragraph of general knowledge is acceptable; a page of it is not.

## Lint

Trigger: the user asks for a health check.

Read-only. The agent reports a punch list and waits:

- Orphan pages with no inbound links.
- Entities marked `active` but untouched for about six months.
- Broken wikilinks.
- Pages missing `projects:`.
- Candidate contradictions between pages.
- Index drift.

Fixes happen only after the user confirms, and each fix is logged.

## Log today

Trigger: "log today", "update the vault with today's work", "end of day".

The agent enumerates today's commits across the user's repositories, greps the current log shard for today's date, diffs the two against the conversation, and ingests the missing workstreams. Commits that were never discussed still get logged; entries already present are not duplicated.

## Killed-session recovery

Trigger: a fresh session is asked "do you have anything in memory to save?"

The honest answer is no: a new session has zero memory of the old one. What it has is the log. It reads the tail of the newest shard to find the last recorded date, runs the commit scan since that date, reports the gap, and asks the user for what commits cannot carry: decisions, conversations, findings. Then it ingests normally.

This is why the log is append-only, one line per action, and dated. It is the only artifact that survives every failure mode.

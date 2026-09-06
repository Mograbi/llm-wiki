# Vault vs. public documents

The vault is meant to be rich. It names colleagues, customers, vendors, hostnames, ticket numbers, and the exact deploy that broke. That is what makes it useful as an engineering memory, and what makes it unpublishable.

Anything that leaves the vault is a different artifact and goes through translation first. This applies to READMEs, design docs written into a code repository, blog posts, and answers pasted into a public channel.

## The rule

- **Vault keeps** provenance: who said what, when, in which conversation; which customer; which host; the decision history and the corrections.
- **Public document keeps** the operational core: what to do, what to check, what the design is, what the lesson was.

## Strip list

When writing from vault content into something public, remove:

- Personal names of colleagues, vendors' staff, customers' staff.
- Customer and partner names. Use generic framing: "a large financial institution", "strict-policy deployments", "a partner platform".
- Conversation provenance: "per X", "as discussed in Y", "via Z's report".
- Dates tied to conversations rather than to releases.
- Hostnames, IPs, device identifiers, ticket numbers, unless they are the subject of the document.
- Anything unreleased or under NDA.

Keep the mechanism, the evidence shape, and the lesson. "Cross-edge correlation between dropped frames and resident memory pointed at the decoder's buffer lifecycle" is publishable. The device IDs and the customer it happened at are not.

## Why the agent, not you, does the translation

The skill tells the agent that the vault is the private layer and that writing into a repo means translating. The agent has the vault page in front of it and can strip on the way out. You review the result. This is faster and more consistent than sanitizing by hand, and it means the vault never has to be self-censored to be safe.

## Before publishing this repository, or your vault

A sweep that has caught real leaks:

```bash
grep -rniE 'acme-corp|jane|10\.0\.|@example\.com|onedrive' . --include='*.md' --include='*.py' --include='*.sh'
```

Replace the alternation with the actual names, customers, hostnames, and email domains you need to keep private. Run it on the repo and on any vault export before it goes anywhere public.

Run as written against this repository it matches `onedrive` in its own notes about sync clients. That is the shape of a false positive: a word that is documentation here and a leak somewhere else. Read every hit; do not automate the judgment.

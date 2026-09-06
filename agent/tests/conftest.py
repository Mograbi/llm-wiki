"""Shared fixture: a mini-vault mirroring the real schema."""

import pytest

PAGES = {
    "entities/relay-tunnel.md": """---
type: entity
projects: [acme]
related: ["[[edge-gateway]]"]
sources: ["[[build-box-access-2026-08-26]]"]
status: active
updated: 2026-08-25
tags: [edge]
---

# Relay tunnel

Product name is Device Relay. Fronted by [[edge-gateway]].

## Retry behavior

Three layers, none retries a device login. See [[control-service]].

## Teardown timers

Idle 120s, hard ceiling 20 min.
""",
    "entities/edge-gateway.md": """---
type: entity
projects: [acme]
status: active
updated: 2026-07-01
tags: [edge]
---

# Edge gateway

TLS front for edge services.
""",
    "entities/control-service.md": """---
type: entity
projects: [acme]
status: active
updated: 2026-08-01
tags: [edge]
---

# control service

Message bus + network config. Talks to [[edge-gateway]].
""",
    "sources/build-box-access-2026-08-26.md": """---
type: source
source_type: conversation
ingested: 2026-08-26
projects: [acme]
entities: ["[[relay-tunnel]]"]
status: summarized
tags: [infra]
---

# Build box access

Recovered SSH access to the shared build box.
""",
    "queries/device-relay-auto-login.md": """---
type: query
asked: 2026-08-25
projects: [acme]
entities: ["[[relay-tunnel]]"]
sources_used: []
---

# Device Relay auto-login?

No auto-login; transport-only. [[relay-tunnel]] is the mechanism.
""",
}


@pytest.fixture
def mini_vault(tmp_path):
    for rel, text in PAGES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    (tmp_path / "_meta").mkdir()
    return tmp_path


class FakeEmbedder:
    """Deterministic hashed bag-of-words embedder, so ranking tests mean something:
    texts sharing words land closer together. Counts calls for incremental tests."""

    dim = 32
    identity = "fake:v1"

    def __init__(self, identity="fake:v1"):
        self.identity = identity
        self.embedded_texts = []

    def embed(self, texts):
        self.embedded_texts += texts
        out = []
        for t in texts:
            vec = [0.0] * self.dim
            for tok in t.lower().split():
                vec[hash(tok) % self.dim] += 1.0
            out.append(vec)
        return out

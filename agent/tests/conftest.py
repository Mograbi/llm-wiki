"""Shared fixture: a mini-vault mirroring the real schema."""

import pytest

PAGES = {
    "entities/frp-reverse-tunnel.md": """---
type: entity
projects: [acme]
related: ["[[envoy-proxy]]"]
sources: ["[[gpu-box-access-2026-08-26]]"]
status: active
updated: 2026-08-25
tags: [edge]
---

# FRP reverse tunnel

Product name is Device VPN. Fronted by [[envoy-proxy]].

## Retry behavior

Three layers, none retries a device login. See [[msgs-service]].

## Teardown timers

Idle 120s, hard ceiling 20 min.
""",
    "entities/envoy-proxy.md": """---
type: entity
projects: [acme]
status: active
updated: 2026-07-01
tags: [edge]
---

# Envoy proxy

TLS front for edge services.
""",
    "entities/msgs-service.md": """---
type: entity
projects: [acme]
status: active
updated: 2026-08-01
tags: [edge]
---

# msgs service

MQTT + network config. Talks to [[envoy-proxy]].
""",
    "sources/gpu-box-access-2026-08-26.md": """---
type: source
source_type: conversation
ingested: 2026-08-26
projects: [acme]
entities: ["[[frp-reverse-tunnel]]"]
status: summarized
tags: [infra]
---

# GPU box access

Recovered SSH access to the shared GPU box.
""",
    "queries/device-vpn-auto-login.md": """---
type: query
asked: 2026-08-25
projects: [acme]
entities: ["[[frp-reverse-tunnel]]"]
sources_used: []
---

# Device VPN auto-login?

No auto-login; transport-only. [[frp-reverse-tunnel]] is the mechanism.
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

"""Optional local embeddings via Ollama. Vault text never leaves the machine.

Ollama may not be installed or running; probe() decides, and everything
degrades to lexical search when it is absent. Configure with:

  WIKI_OLLAMA_URL    default http://localhost:11434
  WIKI_EMBED_MODEL   default nomic-embed-text
  WIKI_NO_EMBED=1    never try, even if Ollama is up
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
BATCH = 32


class OllamaEmbedder:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = (base_url or os.environ.get("WIKI_OLLAMA_URL", DEFAULT_URL)).rstrip("/")
        self.model = model or os.environ.get("WIKI_EMBED_MODEL", DEFAULT_MODEL)

    @property
    def identity(self) -> str:
        return f"ollama:{self.model}"

    def probe(self) -> bool:
        """True when Ollama answers AND the embedding model is pulled."""
        try:
            with urllib.request.urlopen(f"{self.base_url}/api/tags", timeout=3) as r:
                tags = json.load(r)
        except (urllib.error.URLError, OSError, json.JSONDecodeError, ValueError):
            return False
        names = {m.get("name", "").split(":")[0] for m in tags.get("models", [])}
        return self.model.split(":")[0] in names

    def embed(self, texts: list[str]) -> list[list[float]]:
        out: list[list[float]] = []
        for i in range(0, len(texts), BATCH):
            body = json.dumps({"model": self.model, "input": texts[i:i + BATCH]}).encode()
            req = urllib.request.Request(
                f"{self.base_url}/api/embed", data=body,
                headers={"Content-Type": "application/json"},
            )
            with urllib.request.urlopen(req, timeout=120) as r:
                out += json.load(r)["embeddings"]
        return out


def get_embedder() -> OllamaEmbedder | None:
    """The configured embedder if it is reachable and its model is pulled, else None."""
    if os.environ.get("WIKI_NO_EMBED"):
        return None
    e = OllamaEmbedder()
    return e if e.probe() else None

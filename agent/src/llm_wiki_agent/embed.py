"""Optional local embeddings via Ollama.

Every section of every page is POSTed to the embedder, so WIKI_OLLAMA_URL is a
trust boundary: by default only loopback hosts are accepted, and vault text
stays on the machine. Ollama may not be installed or running; probe() decides,
and everything degrades to lexical search when it is absent. Configure with:

  WIKI_OLLAMA_URL            default http://localhost:11434 (loopback only unless...)
  WIKI_OLLAMA_ALLOW_REMOTE=1 ...you explicitly allow sending vault text off-machine
  WIKI_EMBED_MODEL           default nomic-embed-text
  WIKI_NO_EMBED=1            never try, even if Ollama is up
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.parse
import urllib.request

DEFAULT_URL = "http://localhost:11434"
DEFAULT_MODEL = "nomic-embed-text"
BATCH = 32
MAX_RESPONSE_BYTES = 64 * 1024 * 1024
LOOPBACK = {"localhost", "127.0.0.1", "::1", "[::1]", "0.0.0.0"}


class EmbedderError(Exception):
    """The embedder is misconfigured or returned something unusable."""


def _check_url(url: str) -> str:
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise EmbedderError(f"WIKI_OLLAMA_URL must be an http(s) URL, got {url!r}")
    host = parsed.hostname.lower()
    if host not in LOOPBACK and not host.endswith(".localhost") \
            and not os.environ.get("WIKI_OLLAMA_ALLOW_REMOTE"):
        raise EmbedderError(
            f"WIKI_OLLAMA_URL points at {host}, which is not this machine. Vault text would be"
            " sent there. Set WIKI_OLLAMA_ALLOW_REMOTE=1 if that is what you want."
        )
    return url.rstrip("/")


class OllamaEmbedder:
    def __init__(self, base_url: str | None = None, model: str | None = None):
        self.base_url = _check_url(base_url or os.environ.get("WIKI_OLLAMA_URL", DEFAULT_URL))
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
                data = json.loads(r.read(MAX_RESPONSE_BYTES + 1)[:MAX_RESPONSE_BYTES] or b"{}")
            vectors = data.get("embeddings") if isinstance(data, dict) else None
            if not isinstance(vectors, list) or len(vectors) != len(texts[i:i + BATCH]) \
                    or not all(isinstance(v, list) and v and
                               all(isinstance(x, (int, float)) for x in v) for v in vectors):
                raise EmbedderError(f"embedder at {self.base_url} returned an unusable response")
            out += vectors
        return out


def get_embedder() -> OllamaEmbedder | None:
    """The configured embedder if it is reachable and its model is pulled, else None."""
    if os.environ.get("WIKI_NO_EMBED"):
        return None
    e = OllamaEmbedder()
    return e if e.probe() else None

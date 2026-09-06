"""The benchmark script must run end to end on a small vault."""

import subprocess
import sys
from pathlib import Path

BENCH = Path(__file__).resolve().parents[1] / "bench" / "search_bench.py"


def test_bench_runs_on_the_mini_vault(mini_vault, tmp_path, monkeypatch):
    monkeypatch.setenv("WIKI_NO_EMBED", "1")
    q = tmp_path / "q.yaml"
    q.write_text(
        "- question: teardown timers\n  keywords: [teardown]\n"
        "  answers: [entities/relay-tunnel.md]\n"
        "- question: edge gateway\n  keywords: [gateway]\n"
        "  answers: [entities/edge-gateway.md]\n", encoding="utf-8")
    out = subprocess.run([sys.executable, str(BENCH), str(q), "--vault", str(mini_vault)],
                         capture_output=True, text=True)
    assert out.returncode == 0, out.stderr
    assert "| lexical  | 2/2 |" in out.stdout

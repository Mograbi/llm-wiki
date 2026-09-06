"""Benchmark `wiki search` against grep-by-hand on YOUR vault.

    python agent/bench/search_bench.py questions.yaml [--vault PATH] [-k 8]

questions.yaml: a list of {question, keywords, answers}. `keywords` are what
you would grep for if you had to; `answers` are the vault-relative paths that
count as correct. See questions.example.yaml.

Three methods are scored:
  grep     rg each keyword, rank files by keywords matched then hit count. This
           is *kinder* than reality: a real `rg -l` returns an unranked list.
  lexical  wiki search with the embedder disabled (SQLite FTS5 BM25).
  hybrid   wiki search with the embedder, if one is reachable (else skipped).

Reports top-1 / top-3 / top-k hit rates, mean reciprocal rank, latency, and how
many candidate files grep hands back per question. Read-only on the vault; the
index is built in a temporary cache so your real one is untouched.
"""

from __future__ import annotations

import argparse
import collections
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from llm_wiki_agent import config  # noqa: E402
from llm_wiki_agent.embed import get_embedder  # noqa: E402
from llm_wiki_agent.index import build_index  # noqa: E402
from llm_wiki_agent.search import search  # noqa: E402
from llm_wiki_agent.vault import CONTENT_DIRS  # noqa: E402


def grep_rank(vault: Path, keywords: list[str]) -> tuple[list[str], int]:
    dirs = [d for d in CONTENT_DIRS if (vault / d).is_dir()]
    matched: collections.Counter = collections.Counter()
    hits: collections.Counter = collections.Counter()
    for kw in keywords:
        out = subprocess.run(["rg", "-ilc", "--", kw, *dirs], cwd=vault,
                             capture_output=True, text=True).stdout
        for line in out.splitlines():
            path, _, n = line.rpartition(":")
            if path:
                matched[path] += 1
                hits[path] += int(n or 0)
    ranked = sorted(matched, key=lambda p: (-matched[p], -hits[p], p))
    return ranked, len(matched)


def rank_of(ranked: list[str], answers: list[str]) -> int | None:
    for i, p in enumerate(ranked, 1):
        if p in answers:
            return i
    return None


def summarize(name: str, ranks: list, k: int, seconds: float) -> str:
    n = len(ranks)
    top1 = sum(1 for r in ranks if r == 1)
    top3 = sum(1 for r in ranks if r and r <= 3)
    topk = sum(1 for r in ranks if r and r <= k)
    mrr = sum(1 / r for r in ranks if r) / n if n else 0.0
    return (f"| {name:<8} | {top1}/{n} | {top3}/{n} | {topk}/{n} | {mrr:.2f} "
            f"| {seconds / n * 1000:.0f} ms |")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("questions")
    ap.add_argument("--vault", type=Path, default=None)
    ap.add_argument("-k", type=int, default=8)
    args = ap.parse_args()

    vault = (args.vault or config.vault()).resolve()
    cases = yaml.safe_load(Path(args.questions).read_text(encoding="utf-8"))
    if not isinstance(cases, list) or not cases:
        print("questions file must be a non-empty list", file=sys.stderr)
        return 1
    have_rg = shutil.which("rg") is not None
    if not have_rg:
        print("note: ripgrep (rg) not found — grep column skipped\n")

    with tempfile.TemporaryDirectory() as tmp:
        db = Path(tmp) / "index.db"
        embedder = get_embedder()
        t = time.perf_counter()
        build_index(vault, db, embedder)
        print(f"vault: {vault}  ({len(cases)} questions, index built in {time.perf_counter() - t:.1f}s,"
              f" embedder: {'on' if embedder else 'off'})\n")

        print(f"| {'question':<50} | grep | files | lexical | hybrid |")
        print(f"|{'-' * 52}|------|-------|---------|--------|")
        ranks = {"grep": [], "lexical": [], "hybrid": []}
        secs = {"grep": 0.0, "lexical": 0.0, "hybrid": 0.0}
        for c in cases:
            q, kws, answers = c["question"], c.get("keywords", []), c["answers"]
            rg_r, files = None, 0
            if have_rg and kws:
                t = time.perf_counter(); ranked, files = grep_rank(vault, kws); secs["grep"] += time.perf_counter() - t
                rg_r = rank_of(ranked, answers)
            t = time.perf_counter(); lex = rank_of([h.path for h in search(vault, db, q, None, k=args.k).hits], answers); secs["lexical"] += time.perf_counter() - t
            hyb = None
            if embedder:
                t = time.perf_counter(); hyb = rank_of([h.path for h in search(vault, db, q, embedder, k=args.k).hits], answers); secs["hybrid"] += time.perf_counter() - t
            ranks["grep"].append(rg_r); ranks["lexical"].append(lex); ranks["hybrid"].append(hyb)
            f = lambda r: "-" if r is None else str(r)
            print(f"| {q[:50]:<50} | {f(rg_r):>4} | {files:>5} | {f(lex):>7} | {f(hyb):>6} |")

        print(f"\n| method   | top 1 | top 3 | top {args.k} | MRR  | latency |")
        print("|----------|-------|-------|-------|------|---------|")
        if have_rg:
            print(summarize("grep", ranks["grep"], args.k, secs["grep"]))
        print(summarize("lexical", ranks["lexical"], args.k, secs["lexical"]))
        if embedder:
            print(summarize("hybrid", ranks["hybrid"], args.k, secs["hybrid"]))
        if have_rg:
            fs = sorted(c for c in [len(grep_rank(vault, x.get("keywords", []))[0]) for x in cases])
            print(f"\ngrep hands back a median of {fs[len(fs) // 2]} candidate files per question, unranked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""`wiki search`: semantic when it can be, lexical when it must be, honest either way."""

from conftest import FakeEmbedder

from llm_wiki_agent.index import build_index
from llm_wiki_agent.search import search


def test_hybrid_ranks_the_page_whose_section_matches(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    emb = FakeEmbedder()
    build_index(mini_vault, db_path, emb)
    result = search(mini_vault, db_path, "retry behavior teardown timers", emb, k=3)
    assert result.mode == "hybrid"
    assert result.hits[0].path == "entities/frp-reverse-tunnel.md"
    assert result.hits[0].section in ("Retry behavior", "Teardown timers")
    assert result.note == ""


def test_hybrid_returns_one_hit_per_page(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    emb = FakeEmbedder()
    build_index(mini_vault, db_path, emb)
    result = search(mini_vault, db_path, "retry", emb, k=10)
    paths = [h.path for h in result.hits]
    assert len(paths) == len(set(paths))


def test_project_filter_applies_to_hybrid(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    emb = FakeEmbedder()
    build_index(mini_vault, db_path, emb)
    assert search(mini_vault, db_path, "retry", emb, project="other").hits == []
    assert search(mini_vault, db_path, "retry", emb, project="acme").hits


def test_lexical_fallback_when_no_embedder(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    result = search(mini_vault, db_path, "teardown timers", None)
    assert result.mode == "lexical"
    assert "semantic search off" in result.note
    assert result.hits[0].path == "entities/frp-reverse-tunnel.md"
    assert "Teardown" in result.hits[0].snippet or "teardown" in result.hits[0].snippet.lower()


def test_lexical_fallback_when_nothing_embedded_yet(mini_vault, tmp_path):
    """Embedder reachable but reindex never ran with it: say so, still answer."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    result = search(mini_vault, db_path, "envoy", FakeEmbedder())
    assert result.mode == "lexical"
    assert "run `wiki reindex`" in result.note
    assert result.hits[0].path == "entities/envoy-proxy.md"


def test_lexical_weights_title_matches(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    result = search(mini_vault, db_path, "envoy proxy", None)
    assert result.hits[0].path == "entities/envoy-proxy.md"


def test_lexical_respects_project_filter(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    assert search(mini_vault, db_path, "envoy", None, project="other").hits == []


def test_partial_coverage_is_reported(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    emb = FakeEmbedder()
    build_index(mini_vault, db_path, emb)
    page = mini_vault / "entities/envoy-proxy.md"
    page.write_text(page.read_text() + "\nchanged\n", encoding="utf-8")
    build_index(mini_vault, db_path, None)          # embedder down: 4/5 remain
    result = search(mini_vault, db_path, "retry", emb)
    assert result.mode == "hybrid"
    assert "partial coverage: 4 of 5" in result.note


def test_empty_query_yields_no_hits_without_crashing(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    assert search(mini_vault, db_path, "a", None).hits == []


def test_lexical_ignores_question_words(mini_vault, tmp_path):
    """'the', 'what', 'while' appear on every page; they must not decide the ranking."""
    (mini_vault / "entities" / "chatty.md").write_text(
        "---\ntype: entity\nprojects: [acme]\n---\n\n# Chatty\n\n"
        + "the the the what what while the and the for the.\n" * 5, encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    result = search(mini_vault, db_path, "what happens to the teardown timers while idle", None)
    assert result.hits[0].path == "entities/frp-reverse-tunnel.md"
    assert "entities/chatty.md" not in [h.path for h in result.hits]


def test_lexical_uses_fts_with_stemming(mini_vault, tmp_path):
    """Porter stemming: 'retrying' must find 'retries'; 'timer' must find 'timers'."""
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    for q in ("retrying a login", "teardown timer"):
        result = search(mini_vault, db_path, q, None)
        assert result.mode == "lexical"
        assert result.hits[0].path == "entities/frp-reverse-tunnel.md", q


def test_lexical_handles_punctuated_tokens(mini_vault, tmp_path):
    (mini_vault / "entities" / "dot1x.md").write_text(
        "---\ntype: entity\nprojects: [acme]\n---\n\n# Port auth\n\nWired 802.1x with certs.\n",
        encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    result = search(mini_vault, db_path, "802.1x certs", None)
    assert result.hits[0].path == "entities/dot1x.md"


def test_semantic_only_when_query_is_all_stopwords(mini_vault, tmp_path):
    db_path = tmp_path / "index.db"
    emb = FakeEmbedder()
    build_index(mini_vault, db_path, emb)
    result = search(mini_vault, db_path, "what is it", emb)
    assert result.mode == "semantic"


def test_fusion_lifts_a_page_both_rankers_like(mini_vault, tmp_path):
    """A page ranked well by both lists beats a page only one list likes."""
    from llm_wiki_agent.search import Hit, _fuse
    sem = [Hit("a", "", "", 0.9), Hit("b", "", "", 0.8), Hit("c", "", "", 0.7)]
    lex = [Hit("c", "", "", 9.0), Hit("z", "", "", 8.0), Hit("b", "", "", 7.0)]
    fused = [h.path for h in _fuse(sem, lex)]
    assert fused[:2] == ["c", "b"]        # pages in both lists come first
    assert fused.index("a") > fused.index("b")   # 1st in one list loses to 3rd-and-2nd
    assert set(fused) == {"a", "b", "c", "z"}


def test_fusion_guarantees_each_rankers_first_choice_a_top3_slot():
    """A rare exact token only BM25 sees must not be buried by consensus pages."""
    from llm_wiki_agent.search import Hit, _fuse
    both = [Hit(f"p{i}", "", "", 1.0) for i in range(12)]
    sem = both
    lex = [Hit("rare-token-page", "", "", 9.0)] + both[::-1]   # #1 only here
    fused = [h.path for h in _fuse(sem, lex)]
    assert "rare-token-page" in fused[:3]
    assert fused[0] != "rare-token-page"            # promoted, not forced to the top


def test_lexical_handles_non_latin_queries(mini_vault, tmp_path):
    """The user's vault may be in Hebrew, Arabic or CJK; tokenizing must not be ASCII-only."""
    (mini_vault / "entities" / "hebrew.md").write_text(
        "---\ntype: entity\nprojects: [acme]\n---\n\n# תיעוד עברי\n\nהמצלמה מתנתקת כל לילה.\n",
        encoding="utf-8")
    (mini_vault / "entities" / "arabic.md").write_text(
        "---\ntype: entity\nprojects: [acme]\n---\n\n# توثيق\n\nالكاميرا تنقطع كل ليلة.\n",
        encoding="utf-8")
    db_path = tmp_path / "index.db"
    build_index(mini_vault, db_path, None)
    assert search(mini_vault, db_path, "המצלמה מתנתקת", None).hits[0].path == "entities/hebrew.md"
    assert search(mini_vault, db_path, "الكاميرا", None).hits[0].path == "entities/arabic.md"

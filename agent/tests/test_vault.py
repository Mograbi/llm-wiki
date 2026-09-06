"""Page parsing edge cases found in the real vault."""

from llm_wiki_agent.vault import parse_page

MALFORMED = """---
type: source
source_type: conversation
ingested: 2026-08-17
projects: [acme]
related: ["[[first-page]], "[[second-page]]"]
status: summarized
last_change: findings downgraded: the grid HONOURS virtual-hosted style
---

# Malformed frontmatter page

Body with a [[body-link]].
"""


def test_malformed_yaml_salvages_scalar_keys(tmp_path):
    (tmp_path / "sources").mkdir(parents=True)
    (tmp_path / "sources/bad.md").write_text(MALFORMED, encoding="utf-8")
    page = parse_page(tmp_path, "sources/bad.md")
    # full YAML fails, but scalar keys must survive
    assert page.frontmatter.get("type") == "source"
    assert page.frontmatter.get("status") == "summarized"
    assert str(page.frontmatter.get("ingested")) == "2026-08-17"
    assert page.frontmatter.get("projects") == ["acme"]
    # wikilinks come from raw text, unaffected by YAML validity
    assert "first-page" in page.wikilinks
    assert "second-page" in page.wikilinks
    assert "body-link" in page.wikilinks
    assert page.title == "Malformed frontmatter page"

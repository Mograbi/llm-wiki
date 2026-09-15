"""`wiki lint`: the mechanical checks (orphans, broken wikilinks, missing projects, stale-active)
as SQL over the index. Judgment checks (contradictions) are the agent's and are not here."""

from datetime import date

import pytest

from llm_wiki_agent import cli
from llm_wiki_agent.index import build_index
from llm_wiki_agent.lint import Defect, mechanical_lint

PAGES = {
    "_meta/schema.md": "---\ntype: meta\n---\n# Schema\n",
    "entities/hub.md": (
        "---\ntype: entity\nprojects: [acme]\nstatus: active\nupdated: 2026-09-01\n---\n"
        "# Hub\n\nLinks to [[leaf]], to [[ghost-page]], and to the [[_meta/schema]].\n"
    ),
    "entities/leaf.md": (
        "---\ntype: entity\nprojects: [acme]\nstatus: active\nupdated: 2026-09-01\n---\n"
        "# Leaf\n\nBack to [[hub]].\n"
    ),
    "sources/untagged.md": (
        "---\ntype: source\nprojects: []\nstatus: summarized\ningested: 2026-09-01\n---\n"
        "# Untagged\n\nMentions [[hub]].\n"
    ),
}


@pytest.fixture
def lint_vault(tmp_path):
    for rel, text in PAGES.items():
        p = tmp_path / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return tmp_path


def _keys(defects):
    return {(d.kind, d.page, d.target) for d in defects}


def test_finds_one_of_each_mechanical_defect(lint_vault, tmp_path):
    db = tmp_path / "idx.db"
    build_index(lint_vault, db, None)
    assert _keys(mechanical_lint(lint_vault, db, today=date(2026, 9, 15))) == {
        ("orphan", "sources/untagged.md", ""),
        ("broken_link", "entities/hub.md", "ghost-page"),
        ("missing_projects", "sources/untagged.md", ""),
    }


def test_links_into_meta_are_not_broken(lint_vault, tmp_path):
    db = tmp_path / "idx.db"
    build_index(lint_vault, db, None)
    targets = {d.target for d in mechanical_lint(lint_vault, db, today=date(2026, 9, 15))}
    assert "_meta/schema" not in targets


def test_stale_active_respects_the_threshold(lint_vault, tmp_path):
    db = tmp_path / "idx.db"
    build_index(lint_vault, db, None)
    late = mechanical_lint(lint_vault, db, today=date(2027, 6, 1), stale_days=180)
    assert [d.kind for d in late].count("stale_active") == 2
    assert not [d for d in mechanical_lint(lint_vault, db, today=date(2026, 9, 15)) if d.kind == "stale_active"]


def test_defect_key_is_hashable_and_stable():
    d = Defect("broken_link", "entities/hub.md", "ghost-page")
    assert d.key == ("broken_link", "entities/hub.md", "ghost-page")
    assert len({d, Defect("broken_link", "entities/hub.md", "ghost-page")}) == 1


def test_cli_exit_code_and_json(lint_vault, tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("WIKI_VAULT", str(lint_vault))
    monkeypatch.setenv("WIKI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "get_embedder", lambda: None)
    assert cli.main(["lint", "--json"]) == 1            # defects found -> non-zero, for hooks
    out = capsys.readouterr().out
    import json
    report = json.loads(out)
    assert report["counts"]["broken_link"] == 1 and report["counts"]["orphan"] == 1
    assert {d["kind"] for d in report["defects"]} == {"orphan", "broken_link", "missing_projects"}


def test_cli_clean_vault_exits_zero(lint_vault, tmp_path, monkeypatch, capsys):
    (lint_vault / "entities/hub.md").write_text(
        "---\ntype: entity\nprojects: [acme]\nstatus: active\nupdated: 2026-09-01\n---\n# Hub\n\n[[leaf]] and [[untagged]].\n")
    (lint_vault / "sources/untagged.md").write_text(
        "---\ntype: source\nprojects: [acme]\nstatus: summarized\ningested: 2026-09-01\n---\n# Untagged\n\n[[hub]].\n")
    monkeypatch.setenv("WIKI_VAULT", str(lint_vault))
    monkeypatch.setenv("WIKI_CACHE", str(tmp_path / "cache"))
    monkeypatch.setattr(cli, "get_embedder", lambda: None)
    assert cli.main(["lint"]) == 0
    assert "no mechanical defects" in capsys.readouterr().out

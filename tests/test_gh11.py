"""Regression tests for gh-11: a validated `[[doc#item]]` reference can still render as a dead
in-page link, because whether the item gets an `id=` anchor depends on the prefab that presents it.

`check_refs` proves the item exists in the *data*. These tests cover the other half - that the
anchor exists in the *rendering* - which `Renderer.dead_refs()` derives after a build by comparing
every reference it turned into an href against the `id=` attributes the pages actually emitted.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import BuildError, build, load_all  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

DOC = """\
kind: generic
id: gh11
title: GH11 fixture
summary: Two widgets and a note that references both of them.
status: active
updated: '2026-09-03'
data:
  widgets:
    - id: w-one
      title: The first widget
    - id: w-two
      title: The second widget
  notes:
    - "Read [[gh11#w-one]] before [[gh11#w-two]]."
"""

PAGE_HEAD = """\
id: gh11
route: /gh11/
title: GH11 fixture
summary: Presents the fixture doc so the references in it become real links.
nav: {hidden: true}
docs: [gh11]
sections:
  - id: notes
    type: prefab
    prefab: bullet-list
    args:
      items: {from: gh11.data.notes}
"""

# The widgets in a prefab that anchors every row that carries an id.
PAGE_TABLE = PAGE_HEAD + """\
  - id: widgets
    type: prefab
    prefab: table
    args:
      rows: {from: gh11.data.widgets}
"""

# The same widgets through a prefab that emits no id at all.
PAGE_PLAIN = PAGE_HEAD + """\
  - id: widgets
    type: prefab
    prefab: gh11-plain
    args:
      items: {from: gh11.data.widgets}
"""

# The widgets are on the page, but the binding filters w-two out of it entirely.
PAGE_FILTERED = PAGE_HEAD + """\
  - id: widgets
    type: prefab
    prefab: table
    args:
      rows: {from: gh11.data.widgets, where: {id: w-one}}
"""

PLAIN_PREFAB = """\
name: gh11-plain
description: Test fixture - lists item titles and deliberately emits no anchor.
category: list
params:
  items: {type: list, required: true}
template: |
  <ul class="gh11-plain">{% for it in items %}<li>{{ it.title }}</li>{% endfor %}</ul>
examples:
  - args: {items: [{id: a, title: A}]}
"""

# Dead references this repository is allowed to carry: none. It had six when this check was
# written - `plan` presented its open questions with `where: {status: open}`, so every answered
# question was absent from /plan/ while the worksheets that decided them linked straight at it.
# Anchoring could not fix those; the page had to show the items, and it now does (an
# answered-questions section). Keep this empty: an entry here is a dead link somebody decided to
# live with, and it should be argued for in the commit that adds it.
KNOWN_DEAD: set[str] = set()


class Gh11Case(TempSiteCase):
    def write_fixture(self, page: str, plain_prefab: bool = False) -> None:
        self.write_doc("gh11.yaml", DOC)
        (self.root / "site" / "pages" / "gh11.yaml").write_text(page, encoding="utf-8")
        if plain_prefab:
            prefabs = self.root / "site" / "prefabs"
            prefabs.mkdir(parents=True, exist_ok=True)
            (prefabs / "gh11-plain.yaml").write_text(PLAIN_PREFAB, encoding="utf-8")

    def warnings(self, strict: bool = False, only: str = "gh11#") -> list[str]:
        """Dead-reference warnings from a private build, narrowed to the fixture doc by default
        (this repository carries its own known-dead references - see KNOWN_DEAD)."""
        report = build(self.cfg, "private", out_dir=self.root / "out", strict=strict, run_dynamic=False)
        return [w for w in report.warnings if w.startswith("dead reference") and only in w]


class DeadReferenceDetectionTests(Gh11Case):
    def test_a_reference_whose_prefab_emits_no_anchor_is_caught(self):
        self.write_fixture(PAGE_PLAIN, plain_prefab=True)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])  # validate is happy: the items exist in the data
        dead = self.warnings()
        self.assertEqual(len(dead), 2, dead)
        joined = "\n".join(dead)
        self.assertIn("[[gh11#w-one]]", joined)
        self.assertIn("[[gh11#w-two]]", joined)

    def test_the_failure_names_the_reference_the_page_and_the_missing_anchor(self):
        self.write_fixture(PAGE_PLAIN, plain_prefab=True)
        message = next(w for w in self.warnings() if "w-two" in w)
        self.assertIn("[[gh11#w-two]]", message)          # the reference
        self.assertIn("/gh11/", message)                  # the page that carries the link
        self.assertIn("emits no anchor 'w-two'", message)  # what is missing
        self.assertIn("docs/gh11.yaml", message)          # where the reference was written

    def test_a_properly_anchored_reference_passes(self):
        self.write_fixture(PAGE_TABLE)
        self.assertEqual(self.warnings(), [])

    def test_an_item_filtered_off_its_own_page_is_caught(self):
        """The other half of gh-11: the prefab anchors fine, but the binding drops the item."""
        self.write_fixture(PAGE_FILTERED)
        dead = self.warnings()
        self.assertEqual(len(dead), 1, dead)
        self.assertIn("[[gh11#w-two]]", dead[0])

    def test_strict_build_fails_on_a_dead_reference(self):
        self.write_fixture(PAGE_PLAIN, plain_prefab=True)
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", out_dir=self.root / "out", strict=True, run_dynamic=False)
        self.assertIn("dead reference", str(ctx.exception))

    def test_an_anchored_reference_survives_a_strict_build(self):
        """Strict is fatal for dead references the way it already is for stale evidence and
        flagged strings (adr-014), so an anchored reference must contribute nothing to that
        failure. This repository has no dead references left, so a strict build of it plus the
        anchored fixture has to succeed outright - which also pins the repository itself clean."""
        self.write_fixture(PAGE_TABLE)
        build(self.cfg, "private", out_dir=self.root / "out", strict=True, run_dynamic=False)


class PrefabAnchorTests(Gh11Case):
    """An item-presenting prefab must anchor any item that carries an `id`, using the id verbatim
    so `[[doc#item]]` keeps working."""

    def renderer(self) -> Renderer:
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        return Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)

    def test_table_anchors_a_row_that_carries_an_id(self):
        html = self.renderer().prefab("table", {"rows": [{"id": "scp.5", "title": "Balance work"}]})
        self.assertIn('id="scp.5"', html)

    def test_table_without_ids_is_unchanged(self):
        html = self.renderer().prefab("table", {"rows": [{"title": "No id here"}]})
        self.assertIn("<tr>", html)

    def test_table_tolerates_rows_that_are_not_mappings(self):
        html = self.renderer().prefab("table", {"rows": ["plain", "strings"], "columns": ["value"]})
        self.assertIn("plain", html)

    def test_timeline_anchors_an_entry_that_carries_an_id(self):
        html = self.renderer().prefab("timeline", {"items": [{"id": "rel-1", "title": "First"}]})
        self.assertIn('id="rel-1"', html)

    def test_bullet_list_anchors_an_item_that_carries_an_id(self):
        html = self.renderer().prefab("bullet-list", {"items": [{"id": "g-1", "text": "A goal"}]})
        self.assertIn('id="g-1"', html)

    def test_bullet_list_of_plain_strings_still_renders(self):
        html = self.renderer().prefab("bullet-list", {"items": ["one", "two"]})
        self.assertIn("one", html)
        self.assertNotIn("id=", html)

    def test_term_list_anchors_the_term_id_and_keeps_the_term_prefix(self):
        html = self.renderer().prefab(
            "term-list", {"terms": [{"id": "structured-doc", "term": "structured doc", "definition": "A doc."}]}
        )
        self.assertIn('id="structured-doc"', html)
        self.assertIn('id="term-structured-doc"', html)  # see_also links still resolve

    def test_changelog_list_anchors_a_release_that_carries_an_id(self):
        html = self.renderer().prefab(
            "changelog-list", {"releases": [{"id": "r030", "version": "0.3.0", "changes": []}]}
        )
        self.assertIn('id="r030"', html)
        self.assertIn('id="v0-3-0"', html)


class ThisRepositoryTests(Gh11Case):
    def test_no_new_dead_references_in_this_repository(self):
        for target in ("private", "public"):
            with self.subTest(target=target):
                report = build(self.cfg, target, out_dir=self.root / f"out-{target}", run_dynamic=False)
                dead = {
                    w.split("[[", 1)[1].split("]]", 1)[0]
                    for w in report.warnings
                    if w.startswith("dead reference")
                }
                self.assertLessEqual(dead, KNOWN_DEAD, sorted(dead - KNOWN_DEAD))

    def test_the_risk_table_references_this_issue_reproduced_now_resolve(self):
        """`plan` presents its risks with the `table` prefab and other docs cite them by id."""
        report = build(self.cfg, "private", out_dir=self.root / "out", run_dynamic=False)
        dead = "\n".join(w for w in report.warnings if w.startswith("dead reference"))
        for risk in ("r-evidence-noise", "r-headers", "r-two-renderers"):
            self.assertNotIn(risk, dead)
        page = (self.root / "out" / "plan" / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="r-evidence-noise"', page)

    def test_prefab_examples_do_not_ship_dead_reference_links(self):
        report = build(self.cfg, "private", out_dir=self.root / "out", run_dynamic=False)
        dead = "\n".join(w for w in report.warnings if w.startswith("dead reference"))
        self.assertNotIn("/reference/", dead)


if __name__ == "__main__":
    unittest.main()

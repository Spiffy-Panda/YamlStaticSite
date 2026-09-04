"""Regression tests for gh-25: a wide table had nowhere to go.

Nothing in the stylesheet or in any prefab gave a table - or a wrapper around one - an
`overflow-x`. On a wide viewport a table pushed past the content column; on a narrow one the
browser force-wrapped every cell and the table collapsed into an unreadable stack. `evidence-list`
is the worst case, with five columns of which two are monospace and one is free text, and the
check-results table is the main thing those pages exist to show.

The pattern was already established for every other wide thing in the codebase - `pre`,
`.source-pane pre`, `.status-board` all get their own horizontal scroller. Tables were the one wide
thing left out.

The wrapper is the conventional answer and the right one here: `display: block` on a table makes it
scrollable by destroying its table layout, and `max-width` with `contain` behaves differently
across engines.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build  # noqa: E402
from yss.render import default_markdown  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

PREFAB_DIRS = (REPO / "yss" / "prefabs", REPO / "site" / "prefabs")


class EveryTableEmittingPrefabWrapsTests(unittest.TestCase):
    def test_no_prefab_emits_a_bare_table(self):
        """Found by grep rather than by trusting a count - there are five, not the two reported."""
        offenders = []
        for directory in PREFAB_DIRS:
            for path in sorted(directory.glob("*.yaml")):
                text = path.read_text(encoding="utf-8")
                for match in re.finditer(r"<table[^>]*>", text):
                    before = text[: match.start()]
                    # The wrapper is the element immediately before the table in the template.
                    if not re.search(r'<div class="table-scroll">\s*$', before):
                        offenders.append(f"{path.name}: {match.group(0)}")
        self.assertEqual(offenders, [], "every prefab table needs its own scroll container")

    def test_the_wrappers_are_balanced(self):
        for directory in PREFAB_DIRS:
            for path in sorted(directory.glob("*.yaml")):
                text = path.read_text(encoding="utf-8")
                opens = text.count('<div class="table-scroll">')
                if opens:
                    with self.subTest(prefab=path.name):
                        self.assertEqual(opens, text.count("<table"), path.name)
                        self.assertEqual(text.count("<table"), text.count("</table>"), path.name)


class StylesheetTests(unittest.TestCase):
    def test_the_scroll_container_has_a_rule(self):
        css = (REPO / "yss" / "assets" / "yss.css").read_text(encoding="utf-8")
        match = re.search(r"\.table-scroll\s*\{([^}]*)\}", css)
        self.assertIsNotNone(match, "no .table-scroll rule")
        self.assertIn("overflow-x: auto", match.group(1))


class MarkdownTableTests(unittest.TestCase):
    def test_a_markdown_table_is_wrapped_too(self):
        """A markdown table has no template for an author to reach, so the renderer wraps it."""
        html = default_markdown("| a | b |\n| - | - |\n| 1 | 2 |\n")
        self.assertIn('<div class="table-scroll"><table>', html)
        self.assertIn("</table></div>", html)

    def test_markdown_without_a_table_is_untouched(self):
        html = default_markdown("just a paragraph\n")
        self.assertNotIn("table-scroll", html)


class RenderedOutputTests(TempSiteCase):
    def test_every_table_in_the_built_site_sits_in_a_scroll_container(self):
        """The end-to-end assertion: whatever produced the table, it is contained."""
        build(self.cfg, "private", run_dynamic=False)
        checked = 0
        for path in (self.root / "dist" / "private").rglob("index.html"):
            html = path.read_text(encoding="utf-8")
            for match in re.finditer(r"<table[^>]*>", html):
                checked += 1
                before = html[: match.start()]
                self.assertRegex(
                    before[-120:],
                    r'<div class="table-scroll">\s*$',
                    f"unwrapped table in {path.relative_to(self.root)}",
                )
        self.assertGreater(checked, 0, "the pilot site should render some tables")


if __name__ == "__main__":
    unittest.main()

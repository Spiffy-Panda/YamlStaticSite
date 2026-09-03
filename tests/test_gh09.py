"""Regression tests for gh-09: an optional prefab item field named after a dict method
(e.g. `items`) must not crash the render when the field is absent.

Jinja's `it['items']` (and `it.items`) fall back to attribute lookup on KeyError, which finds
the builtin `dict.items` *method* - truthy, so `{% if it['items'] %}` passes and the loop over
a method object blows up with `TypeError: 'builtin_function_or_method' object is not iterable`.
The fix is `it.get('items')`, an explicit dict method call with no such fallback.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class TimelineMissingItemsTests(TempSiteCase):
    def test_entry_without_items_renders_without_crashing(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = renderer.prefab("timeline", {"items": [{"title": "No bullets here"}]})
        self.assertIn("No bullets here", html)
        self.assertNotIn("<ul>", html)

    def test_entry_with_items_still_renders_the_bullet_list(self):
        loaded = load_all(self.cfg)
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = renderer.prefab("timeline", {"items": [{"title": "Has bullets", "items": ["one", "two"]}]})
        self.assertIn("<ul>", html)
        self.assertIn("one", html)
        self.assertIn("two", html)

    def test_mixed_entries_with_and_without_items(self):
        loaded = load_all(self.cfg)
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = renderer.prefab("timeline", {"items": [
            {"title": "First", "items": ["a bullet"]},
            {"title": "Second"},
        ]})
        self.assertIn("First", html)
        self.assertIn("Second", html)
        self.assertIn("a bullet", html)


class StatusBoardGroupItemsAreAlwaysPresentTests(TempSiteCase):
    """Sanity check that status-board's `g['items']` stays safe: group_items() (yss/binding.py)
    always sets an `items` key (possibly to an empty list), so the collision guarded against in
    timeline.yaml cannot happen here. This documents the safe verdict with a running example."""

    def test_group_with_no_matching_members_still_renders(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = renderer.prefab("status-board", {
            "groups": [{"key": "active", "items": [{"title": "Only one"}]}, {"key": "empty", "items": []}],
            "order": ["active", "empty"],
        })
        self.assertIn("Only one", html)
        self.assertIn("board-count\">0<", html)


if __name__ == "__main__":
    unittest.main()

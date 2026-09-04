"""Regression tests for gh-16: the collection-index card footer printed "1 pages" / "1 docs".

The counts stay (they are target-filtered on purpose - a collection whose docs and pages are all
private reads `0 docs / 0 pages` on the public build), but the nouns are now pluralised against
the count they follow.

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


class CollectionCardCountTests(TempSiteCase):
    def render(self, docs, pages) -> str:
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        return str(renderer.prefab("collection-index", {"collections": [{
            "id": "demo",
            "title": "Demo musing",
            "href": "#",
            "docs": docs,
            "pages": pages,
            "order": 1,
        }]}))

    def test_one_each_is_singular(self):
        html = self.render(["demo/plan"], ["/demo/"])
        self.assertIn("<li>1 doc</li>", html)
        self.assertIn("<li>1 page</li>", html)
        self.assertNotIn("1 docs", html)
        self.assertNotIn("1 pages", html)

    def test_zero_each_is_plural(self):
        html = self.render([], [])
        self.assertIn("<li>0 docs</li>", html)
        self.assertIn("<li>0 pages</li>", html)

    def test_many_is_plural(self):
        html = self.render(["a", "b"], ["/x/", "/y/", "/z/"])
        self.assertIn("<li>2 docs</li>", html)
        self.assertIn("<li>3 pages</li>", html)

    def test_missing_lists_still_render_zero(self):
        loaded = load_all(self.cfg)
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = str(renderer.prefab("collection-index", {"collections": [
            {"id": "bare", "title": "Bare musing", "href": "#", "order": 1},
        ]}))
        self.assertIn("<li>0 docs</li>", html)
        self.assertIn("<li>0 pages</li>", html)


if __name__ == "__main__":
    unittest.main()

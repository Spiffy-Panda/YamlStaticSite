"""Regression tests for gh-28: declaring nav.groups could silently empty the top bar.

`deep_merge` replaces a list wholesale, so declaring `nav.groups` opts out of the defaults. The
reserved `collections` id is filtered out of the page-group list, so a site declaring only that
group had zero page groups: `default_group` was `None`, every page resolved to no group, and the
`not out` safety net could not fire because the collections group had already filled `out`. Every
page still rendered at its route, and nothing linked to any of them. No error, no warning, no
`--strict` failure.

There is a quieter second shape: a page naming an undeclared group while at least one page group
exists falls into `groups[0]` and appears under a heading it has nothing to do with. Also silent.

Both are covered by the warning. The trailing unlabelled group covers the drop itself, so the bar
degrades to its pre-gh-18 shape rather than losing links.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class NavGroupCase(TempSiteCase):
    def set_nav(self, block: str) -> Config:
        path = self.root / "site.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "\n" + block, encoding="utf-8")
        return Config.load(self.root)

    def renderer(self, cfg: Config) -> Renderer:
        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        return Renderer(cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)

    def bar(self, renderer: Renderer) -> list[dict]:
        """The top bar as the root template draws it."""
        return renderer._nav_groups([n for n in renderer.nav if not n["collection"]], renderer._collection_nav(None))


class CollectionsOnlyTests(NavGroupCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.set_nav(
            "nav:\n  groups:\n    - {id: collections, label: Musings, menu: true}\n"
        )

    def test_no_page_is_dropped_from_the_bar(self):
        """The whole defect: pages rendered at their routes with nothing linking to them."""
        renderer = self.renderer(self.cfg)
        linked = {item["id"] for group in self.bar(renderer) for item in group["items"]}
        expected = {n["id"] for n in renderer.nav if not n["collection"]}
        self.assertTrue(expected, "the fixture should have root pages to lose")
        self.assertEqual(expected - linked, set(), "every page must still reach the top bar")

    def test_the_orphans_land_in_a_trailing_unlabelled_group(self):
        """Degrade to the pre-gh-18 shape - one unlabelled run of links - not to nothing."""
        groups = self.bar(self.renderer(self.cfg))
        self.assertEqual(groups[-1]["id"], "")
        self.assertEqual(groups[-1]["label"], "")
        self.assertTrue(groups[-1]["items"])

    def test_the_declared_collections_group_still_renders(self):
        ids = [g["id"] for g in self.bar(self.renderer(self.cfg))]
        self.assertIn("collections", ids)

    def test_a_page_naming_an_undeclared_group_is_reported(self):
        renderer = self.renderer(self.cfg)
        warnings = [w for w in renderer.warnings if "nav.group" in w]
        self.assertTrue(warnings, "the silent drop must not stay silent")
        self.assertTrue(any("/plan/" in w for w in warnings), warnings)
        self.assertTrue(any("no page groups declared" in w for w in warnings), warnings)

    def test_collection_pages_are_not_reported(self):
        """The loader defaults a collection page's group to the collection title, and the
        collection's own sub-nav draws it - warning about those would be noise on every build."""
        renderer = self.renderer(self.cfg)
        self.assertEqual(
            [w for w in renderer.warnings if "nav.group" in w and "demo-musing" in w], [],
        )


class UndeclaredGroupTests(NavGroupCase):
    def test_a_page_falling_into_the_first_group_is_reported(self):
        """The quieter shape: the page appears, under a heading it has nothing to do with."""
        cfg = self.set_nav(
            "nav:\n  groups:\n    - {id: content, label: Content}\n"
            "    - {id: collections, label: Musings, menu: true}\n"
        )
        renderer = self.renderer(cfg)
        warnings = [w for w in renderer.warnings if "nav.group" in w]
        self.assertTrue(any("'meta'" in w and "drawn under 'content' instead" in w for w in warnings), warnings)

    def test_a_fully_declared_site_warns_about_nothing(self):
        """The shipped site.yaml declares every group its pages name."""
        renderer = self.renderer(Config.load(self.root))
        self.assertEqual([w for w in renderer.warnings if "nav.group" in w], [])


if __name__ == "__main__":
    unittest.main()

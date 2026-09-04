"""Regression tests for gh-18: every collection got an unconditional pill in the top bar.

The collections were a hard-coded `<span class="nav-group nav-group-collections">` in
default.html - no label, no menu branch, no ordering hook, no way to suppress them - while page
groups had all of that through site.yaml's `nav.groups`. At nine musings the bar wrapped onto a
second row and nothing could be done about it. Collections are now a nav group like any other,
under the reserved id `collections`.

The latent half: `site.schema.yaml` has `additionalProperties: false` and shipped without a `nav`
key at all, and `load_all` validates the raw site.yaml - so `nav:` had ALWAYS been rejected with
"Additional properties are not allowed ('nav' was unexpected)". The knob adr-022 documents was
never turnable, which is why nobody had hit the wrapping problem from the config side.
`test_nav_key_validates` is the pin for that.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.render import COLLECTION_NAV_GROUP  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

NAV_RE = re.compile(r'<nav id="site-nav".*?</nav>', re.S)


class NavGroupCase(TempSiteCase):
    def set_nav(self, nav: dict | None) -> Config:
        path = self.root / "site.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if nav is None:
            data.pop("nav", None)
        else:
            data["nav"] = nav
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        self.cfg = Config.load(self.root)
        return self.cfg

    def make_collection(self, name: str, collection_yaml: str) -> Config:
        folder = self.root / "examples" / name
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "collection.yaml").write_text(collection_yaml, encoding="utf-8")
        (folder / "pages").mkdir(exist_ok=True)
        (folder / "pages" / "index.yaml").write_text(
            "title: Home\nsections:\n  - {id: s, type: markdown, markdown: hi}\n", encoding="utf-8"
        )
        self.cfg = Config.load(self.root)
        return self.cfg

    def nav_html(self, cfg: Config, target: str = "private", route: str = "index.html") -> str:
        report = build(cfg, target, run_dynamic=False)
        html = (report.out_dir / route).read_text(encoding="utf-8")
        match = NAV_RE.search(html)
        self.assertIsNotNone(match, "no site nav in the rendered page")
        return match.group(0)


class DefaultAppearanceTests(NavGroupCase):
    def test_collections_render_through_the_groups_loop_after_the_page_groups(self):
        nav = self.nav_html(self.cfg)
        self.assertIn('<span class="nav-group nav-group-collections">', nav)
        self.assertIn('class="nav-collection ', nav)
        self.assertIn("Demo musing", nav)
        # still last, exactly where the hard-coded span used to sit
        self.assertGreater(nav.index("nav-group-collections"), nav.index("nav-group-meta"))
        self.assertGreater(nav.index("nav-group-collections"), nav.index("nav-group-content"))

    def test_the_default_config_names_collections_last(self):
        ids = [g["id"] for g in self.cfg.nav["groups"]]
        self.assertEqual(ids[-1], COLLECTION_NAV_GROUP)

    def test_the_active_collection_is_marked_on_its_own_pages(self):
        nav = self.nav_html(self.cfg, route="demo-musing/index.html")
        pill = next(line for line in nav.splitlines() if "nav-collection" in line)
        self.assertIn("active", pill)
        # ... and not on a page outside it
        self.assertNotIn("active", next(line for line in self.nav_html(self.cfg).splitlines() if "nav-collection" in line))


class SchemaTests(NavGroupCase):
    def test_nav_key_validates(self):
        """`nav:` in site.yaml was rejected outright until gh-18 - adr-022's knob never worked."""
        cfg = self.set_nav({"groups": [{"id": "collections", "label": "Musings", "menu": True}, {"id": "content", "label": ""}]})
        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])

    def test_an_unknown_key_inside_a_group_is_still_rejected(self):
        cfg = self.set_nav({"groups": [{"id": "content", "labl": "typo"}]})
        loaded = load_all(cfg)
        self.assertTrue(any("labl" in e for e in loaded.errors), loaded.errors)

    def test_a_group_without_an_id_is_rejected(self):
        cfg = self.set_nav({"groups": [{"label": "Nameless"}]})
        loaded = load_all(cfg)
        self.assertTrue(any("id" in e for e in loaded.errors), loaded.errors)


class CustomisedNavTests(NavGroupCase):
    def test_collections_can_be_a_labelled_menu_placed_first(self):
        cfg = self.set_nav({"groups": [{"id": "collections", "label": "Musings", "menu": True}, {"id": "content", "label": ""}]})
        nav = self.nav_html(cfg)
        self.assertIn('<details class="nav-group nav-group-collections nav-menu">', nav)
        self.assertIn("<summary>Musings", nav)
        self.assertLess(nav.index("nav-group-collections"), nav.index("nav-group-content"))
        self.assertIn('class="nav-collection ', nav)

    def test_a_customised_nav_without_collections_still_shows_them(self):
        """Back-compat: a site that customised nav.groups before gh-18 has no `collections` entry."""
        cfg = self.set_nav({"groups": [{"id": "content", "label": ""}, {"id": "decide", "label": "Decide"}]})
        nav = self.nav_html(cfg)
        self.assertIn('<span class="nav-group nav-group-collections">', nav)
        self.assertIn("Demo musing", nav)
        self.assertGreater(nav.index("nav-group-collections"), nav.index("nav-group-decide"))

    def test_a_page_declaring_the_reserved_group_falls_back_to_the_default_group(self):
        (self.root / "site" / "pages" / "sneaky.yaml").write_text(
            "title: Sneaky\nnav: {group: collections}\nsections:\n  - {id: s, type: markdown, markdown: hi}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        nav = self.nav_html(cfg)
        content = nav[nav.index("nav-group-content") : nav.index("nav-group-decide")]
        self.assertIn('href="/sneaky/"', content)
        collections = nav[nav.index("nav-group-collections") :]
        self.assertNotIn("Sneaky", collections)


class VisibilityTests(NavGroupCase):
    def test_a_private_collection_is_marked_private_in_the_private_build(self):
        cfg = self.make_collection("hush-hush", "title: Hush hush\nvisibility: private\n")
        nav = self.nav_html(cfg, "private")
        pill = next(line for line in nav.splitlines() if "Hush hush" in line)
        self.assertIn("nav-collection", pill)
        self.assertIn("private", pill)
        # and absent from the public build entirely
        self.assertNotIn("Hush hush", self.nav_html(cfg, "public"))

    def test_collections_are_ordered_by_order_then_title(self):
        self.make_collection("zeta-one", "title: Zeta\norder: 1\n")
        cfg = self.make_collection("alpha-nine", "title: Alpha\norder: 9\n")
        nav = self.nav_html(cfg)
        self.assertLess(nav.index("Zeta"), nav.index("Alpha"))


if __name__ == "__main__":
    unittest.main()

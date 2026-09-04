"""Regression tests for gh-14: a collection declaring `at:` 404'd its own theme.css and emblem,
and nothing in the toolchain noticed.

Two halves, one issue:

* Four call sites answered "where do this collection's files live" and only one of them honoured
  the route prefix. `collection_url` prefixed `route_prefix` (so the stylesheet href was right),
  while the asset emit, both emblem hrefs and `summary()`'s published `emblem` were all built from
  the collection id. The emblems worked only because emit and href were wrong in the same
  direction, so fixing either alone breaks them. `Collection.route_path()` is now the single
  answer, and the resolved `emblem_url` is published on `$collections` rather than re-derived by
  every consumer. The route-prefix half is pinned in test_gh02_03_04.py, whose fixture had no
  `assets/` and so could not see the bug.
* Nothing caught it. `validate`, `check`, `scan` and `build` were all green over a page whose own
  stylesheet 404'd, because `dead_refs` asks whether an *anchor* exists and nothing asked whether a
  *file* did. `_dead_links` closes that, on rendered pages only (a mount's contents are the
  collection's business - adr-021), and fails the build under --strict like the other output gates.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import BuildError, _dead_links, build  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


def dead(report) -> list[str]:
    return [w for w in report.warnings if w.startswith("dead link")]


class RoutePathHelperTests(TempSiteCase):
    def test_route_path_is_the_one_answer_for_root_and_prefixed_collections(self):
        root = self.cfg.collection("")
        self.assertEqual(root.route_path("assets"), "assets")
        self.assertEqual(root.route_path(), "")
        demo = self.cfg.collection("demo-musing")
        self.assertEqual(demo.route_path("assets"), "demo-musing/assets")
        self.assertEqual(demo.route_path("/assets/theme.css"), "demo-musing/assets/theme.css")
        self.assertEqual(demo.route_path(), "demo-musing")
        demo.route_base = "musings/"
        self.assertEqual(demo.route_path("assets"), "musings/demo-musing/assets")


class DeadLinkGateTests(TempSiteCase):
    PAGE = (
        "title: Dead links\n"
        "nav: {hidden: true}\n"
        "sections:\n"
        "  - id: s\n"
        "    type: markdown\n"
        "    markdown: |\n"
        '      <a href="/nope/">absolute miss</a>\n'
        '      <a href="assets/missing.css">relative miss</a>\n'
    )

    def write_page(self, name: str, text: str) -> Config:
        (self.root / "site" / "pages" / name).write_text(text, encoding="utf-8")
        return Config.load(self.root)

    def test_two_bad_links_on_one_page_are_two_warnings(self):
        cfg = self.write_page("deadlinks.yaml", self.PAGE)
        report = build(cfg, "private", run_dynamic=False)
        found = dead(report)
        self.assertEqual(len(found), 2, found)
        self.assertTrue(any('href="/nope/"' in w and "not in the output" in w for w in found), found)
        self.assertTrue(any('href="assets/missing.css"' in w for w in found), found)
        # the relative one resolves against the page's own directory, not the site root
        self.assertTrue(any("deadlinks/assets/missing.css" in w for w in found), found)
        self.assertTrue(all("/deadlinks/" in w for w in found), found)

    def test_strict_fails_the_build_and_removes_the_output(self):
        cfg = self.write_page("deadlinks.yaml", self.PAGE)
        with self.assertRaises(BuildError) as caught:
            build(cfg, "private", run_dynamic=False, strict=True)
        message = str(caught.exception)
        self.assertIn("dead link", message)
        self.assertIn("2 dead link(s)", message)
        self.assertFalse((self.root / "dist" / "private").exists())

    def test_a_link_into_a_mount_resolves(self):
        """Mount *contents* are not checked, but a page's link into one is like any other link."""
        cfg = self.write_page(
            "intomount.yaml",
            "title: Into the mount\n"
            "nav: {hidden: true}\n"
            "sections:\n"
            "  - id: s\n"
            "    type: markdown\n"
            "    markdown: |\n"
            '      <a href="/demo-musing/play/index.html">the playable</a>\n',
        )
        report = build(cfg, "private", run_dynamic=False)
        self.assertEqual(dead(report), [])
        # the same link on the public build, where that mount is not carried, is dead
        public = build(cfg, "public", run_dynamic=False)
        self.assertTrue(any("play/index.html" in w for w in dead(public)), public.warnings)

    def test_public_target_flags_an_href_outside_base_url(self):
        cfg = self.write_page("deadlinks.yaml", self.PAGE)
        report = build(cfg, "public", run_dynamic=False)
        found = dead(report)
        self.assertTrue(any("outside base_url /YamlStaticSite/" in w for w in found), found)
        self.assertTrue(any('href="/nope/"' in w for w in found), found)

    def test_pilot_has_no_dead_links_on_either_target(self):
        for target in ("private", "public"):
            with self.subTest(target=target):
                report = build(self.cfg, target, run_dynamic=False)
                self.assertEqual(dead(report), [])

    def test_off_site_and_fragment_hrefs_are_left_alone(self):
        out = self.root / "dist" / "probe"
        out.mkdir(parents=True, exist_ok=True)
        html = (
            '<a href="https://example.com/x">a</a><a href="//cdn.example/x">b</a>'
            '<a href="mailto:someone@example.com">c</a><a href="#section">d</a>'
            '<img src="data:image/png;base64,AAA"><a href="javascript:void(0)">e</a><a href="">f</a>'
        )
        self.assertEqual(_dead_links([("/", html)], out, "/"), [])

    def test_query_and_fragment_are_stripped_before_the_lookup(self):
        out = self.root / "dist" / "probe"
        (out / "thing").mkdir(parents=True, exist_ok=True)
        (out / "thing" / "index.html").write_text("x", encoding="utf-8")
        html = '<a href="/thing/?q=1#frag">ok</a><a href="/gone/?q=1">bad</a>'
        found = _dead_links([("/", html)], out, "/")
        self.assertEqual(len(found), 1, found)
        self.assertIn('href="/gone/?q=1"', found[0])


if __name__ == "__main__":
    unittest.main()

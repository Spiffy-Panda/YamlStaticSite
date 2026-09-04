"""Regression tests for gh-24: theme.accent/theme.background were an inline style on <body>.

An inline `style` attribute outranks every author stylesheet, media queries included. A collection
that set the two keys *and* shipped a `theme.css` with a `@media (prefers-color-scheme: dark)`
block therefore got a half-applied theme: light `--bg` and `--accent` from the attribute underneath
dark `--fg` and `--bg-2` from the stylesheet - near-white text on a near-white ground, and inline
`<code>` chips as solid black boxes. Less usable than either theme applied whole.

The keys were a trap for exactly the author who needed them most: someone whose palette outgrows
two colours adds a `theme.css`, and the two keys they already had set sabotage its dark half for
those two tokens only, looking like a broken stylesheet rather than a conflict.

They are now a generated rule in `assets/collections.css`, scoped to the collection class and
linked before any collection's own `theme.css`, so the cascade decides and the collection
stylesheet wins - which is the precedence that was wanted all along.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class TintCase(TempSiteCase):
    """The issue's repro: a collection with both keys and a dark-block stylesheet."""

    def make_tint(self) -> Config:
        tint = self.root / "examples" / "tint"
        (tint / "docs").mkdir(parents=True)
        (tint / "pages").mkdir()
        (tint / "assets").mkdir()
        (tint / "collection.yaml").write_text(
            "title: Tint\n"
            "theme:\n"
            '  accent: "#a94f15"\n'
            '  background: "#e9edeb"\n'
            "  css: [assets/theme.css]\n",
            encoding="utf-8")
        (tint / "assets" / "theme.css").write_text(
            ".collection-tint { --bg: #e9edeb; --fg: #1e2422; --accent: #a94f15; }\n"
            "@media (prefers-color-scheme: dark) {\n"
            "  .collection-tint { --bg: #0b0f0e; --fg: #dfe7e3; --accent: #ff9a4d; }\n"
            "}\n",
            encoding="utf-8")
        (tint / "docs" / "plan.yaml").write_text(
            "kind: plan\nid: plan\ntitle: Tint plan\nstatus: active\nmilestones: []\n", encoding="utf-8")
        (tint / "pages" / "index.yaml").write_text(
            "id: index\ntitle: Tint\nsections: [{id: s, type: markdown, markdown: hello}]\n",
            encoding="utf-8")
        return Config.load(self.root)

    def head_links(self, html: str) -> list[str]:
        return re.findall(r'<link rel="stylesheet" href="([^"]+)"', html)


class GeneratedStylesheetTests(TintCase):
    def setUp(self):
        super().setUp()
        self.cfg = self.make_tint()
        build(self.cfg, "private", run_dynamic=False)
        self.out = self.root / "dist" / "private"
        self.html = (self.out / "tint" / "index.html").read_text(encoding="utf-8")

    def test_the_body_carries_no_inline_style(self):
        """The whole defect was the attribute's specificity; there is no attribute now."""
        body = re.search(r"<body[^>]*>", self.html).group(0)
        self.assertNotIn("style=", body, body)
        self.assertIn("collection-tint", body, "the class the generated rule is scoped to must remain")

    def test_the_colours_are_emitted_as_a_scoped_rule(self):
        css = (self.out / "assets" / "collections.css").read_text(encoding="utf-8")
        self.assertIn(".collection-tint", css)
        self.assertIn("--accent: #a94f15;", css)
        self.assertIn("--bg: #e9edeb;", css)

    def test_the_collection_stylesheet_is_linked_after_the_generated_one(self):
        """Same specificity, so source order is the entire mechanism - pin it."""
        links = self.head_links(self.html)
        self.assertIn("/assets/collections.css", links)
        self.assertIn("/tint/assets/theme.css", links)
        self.assertLess(
            links.index("/assets/collections.css"),
            links.index("/tint/assets/theme.css"),
            "the collection's own stylesheet must be able to override the two keys",
        )


class TargetFilteringTests(TintCase):
    def test_a_private_collections_colours_never_reach_the_public_build(self):
        cfg = self.make_tint()
        path = self.root / "examples" / "tint" / "collection.yaml"
        path.write_text(path.read_text(encoding="utf-8") + "visibility: private\n", encoding="utf-8")
        cfg = Config.load(self.root)
        build(cfg, "public", run_dynamic=False)
        css = (self.root / "dist" / "public" / "assets" / "collections.css").read_text(encoding="utf-8")
        self.assertNotIn("tint", css, "a private collection is absent from the public build entirely")


class ColourValuesAreConstrainedTests(TintCase):
    def test_a_value_that_would_break_out_of_the_rule_fails_validation(self):
        """The value used to be autoescaped into an attribute; a stylesheet is not escaped."""
        self.make_tint()
        path = self.root / "examples" / "tint" / "collection.yaml"
        path.write_text(
            path.read_text(encoding="utf-8").replace('"#a94f15"', '"red; } body { display: none"'),
            encoding="utf-8")
        loaded = load_all(Config.load(self.root))
        self.assertTrue(any("accent" in e for e in loaded.errors), loaded.errors)

    def test_ordinary_colour_notations_are_accepted(self):
        self.make_tint()
        path = self.root / "examples" / "tint" / "collection.yaml"
        for value in ('"rebeccapurple"', '"#abc"', '"rgb(12 34 56 / 0.5)"', '"oklch(0.7 0.1 200)"'):
            with self.subTest(value=value):
                path.write_text(
                    "title: Tint\ntheme:\n"
                    f"  accent: {value}\n"
                    "  css: [assets/theme.css]\n",
                    encoding="utf-8")
                loaded = load_all(Config.load(self.root))
                self.assertEqual([e for e in loaded.errors if "accent" in e], [])


class ShippedExampleTests(TempSiteCase):
    def test_the_demo_collection_still_gets_its_accent(self):
        """examples/demo-musing sets theme.accent and lists theme.css - the pattern the trap keys
        on. Its rendering must not change."""
        build(self.cfg, "private", run_dynamic=False)
        css = (self.root / "dist" / "private" / "assets" / "collections.css").read_text(encoding="utf-8")
        self.assertIn(".collection-demo-musing { --accent: #7a3e9d; }", css)


if __name__ == "__main__":
    unittest.main()

"""Regression tests for gh-26: the collection bar had no on-accent foreground token.

`.collection-bar` paints itself with `--accent-2` and coloured everything inside it with `--fg`
and `--fg-2` - tokens defined for use against `--bg`. Nothing in the token set named a foreground
for an accent surface, so a theme that picks a saturated `--accent-2` got a bar whose title and
sub-nav links are nearly invisible: 1.06:1 for the links and 1.63:1 for the title on the flagship
consumer's theme, in both colour schemes.

The stock palettes hide it, because both default `--accent-2` values are near-neutral tints chosen
to sit close to `--bg`. The rule only breaks when a theme uses `--accent-2` as an actual secondary
accent - which is what the token is documented to be.

The fix is four optional tokens consumed with the old values as `var()` fallbacks. Two things are
worth pinning: that the fallbacks are at the point of use rather than declared on `:root`, and that
the stock stylesheet's rendering is unchanged.

Why not `:root`: a custom property resolves its `var()` on the element that *declares* it. A
`:root { --on-accent-2: var(--fg) }` computes the site's `--fg` once and inherits that literal
colour, so a collection theming `--fg` on its own body class would silently lose the link between
the two - the opposite of what the token is for.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

RAW = (REPO / "yss" / "assets" / "yss.css").read_text(encoding="utf-8")
# Comments are prose about the rules, including a counter-example of what NOT to write.
CSS = re.sub(r"/\*.*?\*/", "", RAW, flags=re.S)

TOKENS = ("--collection-bar-bg", "--on-accent-2", "--on-accent-2-muted", "--on-accent-2-surface")


def rule(selector: str) -> str:
    match = re.search(re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert match, f"no rule for {selector}"
    return match.group(1)


class OnAccentTokensTests(unittest.TestCase):
    def test_the_bar_takes_its_background_from_a_named_token(self):
        body = rule(".collection-bar")
        self.assertIn("var(--collection-bar-bg, var(--accent-2))", body)

    def test_the_collection_title_no_longer_takes_a_token_defined_against_the_page(self):
        body = rule(".collection-title")
        self.assertIn("var(--on-accent-2, var(--fg))", body)
        self.assertNotIn("color: var(--fg)", body)

    def test_resting_sub_nav_links_take_the_muted_on_accent_token(self):
        body = rule(".sub-nav a")
        self.assertIn("var(--on-accent-2-muted, var(--fg-2))", body)
        self.assertNotIn("color: var(--fg-2)", body)

    def test_the_active_pill_sits_on_an_on_accent_surface(self):
        """--bg-2 is the *page* surface: on a --bg page the pill is an invisible tint, and on the
        bar it becomes a near-black block. It was specified against a surface it is not on."""
        body = rule(".sub-nav a.active, .sub-nav a:hover")
        self.assertIn("var(--on-accent-2-surface, var(--bg-2))", body)
        self.assertNotIn("background: var(--bg-2)", body)


class DefaultsStayOutOfRootTests(unittest.TestCase):
    def test_no_new_token_is_declared_in_root_or_the_dark_block(self):
        """Declaring them would freeze the site-level value and defeat collection theming."""
        blocks = re.findall(r":root\s*\{([^}]*)\}", CSS)
        self.assertTrue(blocks, "the stylesheet should still have a :root block")
        for token in TOKENS:
            for block in blocks:
                self.assertNotIn(f"{token}:", block, f"{token} must not be declared on :root")

    def test_every_use_carries_the_old_value_as_its_fallback(self):
        """A theme that declares none of them must render exactly as it did before."""
        for token, fallback in (
            ("--collection-bar-bg", "--accent-2"),
            ("--on-accent-2", "--fg"),
            ("--on-accent-2-muted", "--fg-2"),
            ("--on-accent-2-surface", "--bg-2"),
        ):
            with self.subTest(token=token):
                uses = re.findall(re.escape(f"var({token},") + r"\s*var\((--[a-z0-9-]+)\)", CSS)
                self.assertTrue(uses, f"{token} is never consumed")
                self.assertEqual(set(uses), {fallback})


class DocumentedTests(unittest.TestCase):
    def test_the_optional_tokens_are_recorded_for_theme_authors(self):
        text = (REPO / "docs" / "palettes.yaml").read_text(encoding="utf-8")
        for token in TOKENS:
            self.assertIn(token.lstrip("-"), text, f"{token} is undocumented in docs/palettes.yaml")

    def test_they_are_absent_from_the_playground_token_roster(self):
        """`data.tokens` drives the theme playground, which expects a colour per palette; these
        are optional and have no palette values."""
        import yaml

        data = yaml.safe_load((REPO / "docs" / "palettes.yaml").read_text(encoding="utf-8"))["data"]
        keys = {t["key"] for t in data["tokens"]}
        optional = {t["key"] for t in data["optional_tokens"]}
        self.assertEqual(keys & optional, set())
        self.assertEqual(optional, {t.lstrip("-") for t in TOKENS})


if __name__ == "__main__":
    unittest.main()

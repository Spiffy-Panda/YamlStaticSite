"""Regression tests for gh-31: the on-accent tokens gh-26 added had no values anywhere.

gh-26 gave the collection bar four tokens and consumed each with the old value as its `var()`
fallback, so stock palettes rendered unchanged. Nothing ever *declared* them - not `yss.css`, not
the stock palettes, not the generated `collections.css` - so the fallback was always the branch
taken, and the fallback is precisely the broken pairing gh-26 was filed about: `--fg`/`--fg-2` are
specified against `--bg`, not against `--accent-2`. A downstream theme with a saturated
`--accent-2` still shipped a 1.06:1 sub-nav link, now behind a token system that looked like a fix.

The resolution is a build-time gate rather than a derived value. Deriving an on-accent triple into
`collections.css` was considered and rejected: that file is linked *before* a collection's own
`theme.css`, so a derived body-class rule would outrank an author's `:root` declaration - the
generated guess would beat the hand-picked answer. `yss/contrast.py` measures instead, and the
build warns (fatal under `--strict`) below WCAG AA.

The scanner's tolerance is a feature under test here as much as its arithmetic is: a value it
cannot read as a hex literal is *unknown*, and an unknown value is skipped in silence. An author
writing `color-mix()` for their on-accent colour is doing something reasonable, and a checker that
cannot follow them must not fail their build.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss import contrast  # noqa: E402
from yss.contrast import DARK, LIGHT  # noqa: E402

# The saturated theme from the gh-31 report: GameDesignMusings' space-cargo-cannon, with the
# downstream workaround removed. Declares --accent-2 as a real secondary accent and none of the
# on-accent tokens, which is the whole defect in four lines of CSS.
SATURATED = """
/* the collection's own theme */
:root {
  --bg: #ffffff; --bg-2: #ffffff; --fg: #1d1d1b; --fg-2: #5c5c57;
  --accent: #1d7460; --accent-2: #1d7460;
}
@media (prefers-color-scheme: dark) {
  :root { --bg: #17181a; --bg-2: #202225; --fg: #e8e8e4; --fg-2: #b4b4ae;
          --accent: #55c99f; --accent-2: #55c99f; }
}
"""

# The same theme with the triple the downstream repo declared, which is where the tokens are meant
# to live. All six pairings clear AA once they have values.
SATURATED_FIXED = SATURATED.replace(
    "--accent: #1d7460; --accent-2: #1d7460;",
    "--accent: #1d7460; --accent-2: #1d7460;"
    " --on-accent-2: #f2f5f3; --on-accent-2-muted: #e9edeb; --on-accent-2-surface: #f2f5f3;",
).replace(
    "--accent: #55c99f; --accent-2: #55c99f; }",
    "--accent: #55c99f; --accent-2: #55c99f;"
    " --on-accent-2: #0b0f0e; --on-accent-2-muted: #121817; --on-accent-2-surface: #121817; }",
)

STOCK = (REPO / "yss" / "assets" / "yss.css").read_text(encoding="utf-8")


class LuminanceAndRatioTests(unittest.TestCase):
    """WCAG 2.x arithmetic, pinned against values the spec itself states."""

    def test_black_on_white_is_the_maximum_ratio(self):
        self.assertAlmostEqual(contrast.contrast_ratio("#000000", "#ffffff"), 21.0, places=6)

    def test_the_ratio_is_symmetric(self):
        self.assertEqual(
            contrast.contrast_ratio("#000", "#fff"), contrast.contrast_ratio("#fff", "#000")
        )

    def test_a_colour_against_itself_is_one_to_one(self):
        self.assertAlmostEqual(contrast.contrast_ratio("#7b5cff", "#7b5cff"), 1.0, places=9)

    def test_luminance_endpoints(self):
        self.assertAlmostEqual(contrast.relative_luminance("#000000"), 0.0, places=9)
        self.assertAlmostEqual(contrast.relative_luminance("#ffffff"), 1.0, places=9)

    def test_luminance_is_channel_weighted_not_a_grey_average(self):
        """Pure green is far brighter than pure blue at the same channel value (0.7152 vs 0.0722)."""
        self.assertAlmostEqual(contrast.relative_luminance("#00ff00"), 0.7152, places=4)
        self.assertAlmostEqual(contrast.relative_luminance("#0000ff"), 0.0722, places=4)
        self.assertAlmostEqual(contrast.relative_luminance("#ff0000"), 0.2126, places=4)

    def test_a_known_middle_ratio(self):
        """#777 on white is the canonical 'just under AA' grey: 4.48:1."""
        self.assertAlmostEqual(contrast.contrast_ratio("#777777", "#ffffff"), 4.48, places=2)

    def test_shorthand_hex_expands_by_digit_doubling(self):
        self.assertEqual(contrast.parse_hex("#abc"), (0xAA, 0xBB, 0xCC))
        self.assertEqual(contrast.normalize_hex("#ABC"), "#aabbcc")
        self.assertEqual(
            contrast.contrast_ratio("#abc", "#aabbcc"), contrast.contrast_ratio("#aabbcc", "#aabbcc")
        )

    def test_the_reported_shape_of_the_defect(self):
        """A stock foreground on a saturated accent lands near 1:1 - the gh-31 failure mode.

        gh-31 reports 1.63:1 light and 1.06:1 dark for the sub-nav link. Those exact figures came
        from the downstream theme's own `--fg-2`, which the report does not print, so what is
        pinned here is the pairing the report names - stock `--fg-2` against its `--accent-2` -
        rather than numbers this repository cannot reproduce.
        """
        self.assertLess(contrast.contrast_ratio("#b4b4ae", "#55c99f"), 1.1)  # dark
        self.assertLess(contrast.contrast_ratio("#5c5c57", "#1d7460"), 1.5)  # light

    def test_non_hex_colours_are_unknown_rather_than_an_error(self):
        for value in ("var(--fg)", "color-mix(in srgb, var(--accent) 14%, var(--bg-2))",
                      "oklch(62% 0.19 25)", "rebeccapurple", "#12345", "", None, 17):
            self.assertIsNone(contrast.parse_hex(value), value)
            self.assertIsNone(contrast.relative_luminance(value), value)
            self.assertIsNone(contrast.contrast_ratio(value, "#ffffff"), value)

    def test_eight_digit_hex_is_unknown_because_alpha_composites(self):
        self.assertIsNone(contrast.parse_hex("#11223344"))


class ScannerTests(unittest.TestCase):
    def test_both_blocks_are_read_and_kept_apart(self):
        scan = contrast.scan_stylesheet(SATURATED)
        self.assertEqual(scan[LIGHT]["accent-2"], "#1d7460")
        self.assertEqual(scan[DARK]["accent-2"], "#55c99f")
        self.assertNotIn("on-accent-2", scan[LIGHT])

    def test_declarations_outside_root_are_ignored(self):
        scan = contrast.scan_stylesheet(
            ".in-collection .collection-bar { --accent-2: #ff0000; background: red; }"
            ":root { --accent-2: #00ff00; }"
        )
        self.assertEqual(scan[LIGHT]["accent-2"], "#00ff00")

    def test_comments_do_not_leak_declarations(self):
        scan = contrast.scan_stylesheet(
            ":root { --accent: #1f6b52; /* --accent: #000000; a counter-example */ }"
        )
        self.assertEqual(scan[LIGHT], {"accent": "#1f6b52"})

    def test_a_non_dark_media_block_contributes_to_neither_scheme(self):
        scan = contrast.scan_stylesheet(
            ":root { --accent-2: #ffffff; }"
            "@media print { :root { --accent-2: #000000; } }"
            "@media (max-width: 720px) { :root { --accent-2: #123456; } }"
        )
        self.assertEqual(scan[LIGHT]["accent-2"], "#ffffff")
        self.assertEqual(scan[DARK], {})

    def test_unknown_values_survive_the_scan_as_written(self):
        """They have to reach `resolve_color` to be recognised as declared-but-unreadable."""
        scan = contrast.scan_stylesheet(":root { --on-accent-2: color-mix(in srgb, #fff 80%, #000); }")
        self.assertEqual(scan[LIGHT]["on-accent-2"], "color-mix(in srgb, #fff 80%, #000)")

    def test_shorthand_hex_in_a_stylesheet(self):
        scan = contrast.scan_stylesheet(":root{--fg:#000;--accent-2:#FFF}")
        self.assertEqual(contrast.resolve_color(scan[LIGHT], ("fg",)), "#000000")
        self.assertEqual(contrast.resolve_color(scan[LIGHT], ("accent-2",)), "#ffffff")

    def test_the_stock_stylesheet_declares_none_of_the_on_accent_tokens(self):
        """The premise of the whole issue, and what tests/test_gh26.py:71 pins from the other side."""
        scan = contrast.scan_stylesheet(STOCK)
        for token in ("on-accent-2", "on-accent-2-muted", "on-accent-2-surface", "collection-bar-bg"):
            self.assertNotIn(token, scan[LIGHT])
            self.assertNotIn(token, scan[DARK])


class CascadeAndResolutionTests(unittest.TestCase):
    def test_dark_inherits_light_tokens_no_dark_block_overrides(self):
        cascaded = contrast.cascade([contrast.scan_stylesheet(SATURATED)])
        self.assertEqual(cascaded[DARK]["accent-2"], "#55c99f")  # overridden
        self.assertEqual(cascaded[LIGHT]["bg-2"], "#ffffff")

    def test_a_later_stylesheet_wins(self):
        cascaded = contrast.cascade(
            [
                contrast.scan_stylesheet(":root { --accent-2: #111111; }"),
                contrast.scan_stylesheet(":root { --accent-2: #222222; }"),
            ]
        )
        self.assertEqual(cascaded[LIGHT]["accent-2"], "#222222")

    def test_a_later_light_declaration_beats_an_earlier_dark_one(self):
        """Document order, not scheme: `collections.css` is linked after the site theme."""
        cascaded = contrast.cascade(
            [
                contrast.scan_stylesheet(
                    ":root { --accent: #111111; }"
                    "@media (prefers-color-scheme: dark) { :root { --accent: #999999; } }"
                ),
                {LIGHT: {"accent": "#7a3e9d"}, DARK: {}},
            ]
        )
        self.assertEqual(cascaded[DARK]["accent"], "#7a3e9d")

    def test_an_undeclared_token_falls_through_to_the_next_link(self):
        tokens = {"fg": "#1d1d1b"}
        self.assertEqual(contrast.resolve_color(tokens, ("on-accent-2", "fg")), "#1d1d1b")

    def test_a_declared_token_shadows_the_fallback_even_when_unreadable(self):
        """`var(--a, var(--b))` never reaches --b once --a has a value, readable or not."""
        tokens = {"on-accent-2": "color-mix(in srgb, #fff 80%, #000)", "fg": "#1d1d1b"}
        self.assertIsNone(contrast.resolve_color(tokens, ("on-accent-2", "fg")))

    def test_a_chain_with_nothing_declared_is_unknown(self):
        self.assertIsNone(contrast.resolve_color({}, ("on-accent-2", "fg")))


class GateTests(unittest.TestCase):
    def test_the_saturated_theme_fails_with_the_reported_message(self):
        cascaded = contrast.cascade([contrast.scan_stylesheet(SATURATED)])
        warnings = contrast.check_theme(cascaded, "collection space-cargo-cannon")
        self.assertEqual(
            warnings,
            [
                "collection space-cargo-cannon (light): collection title #1d1d1b on #1d7460 = "
                "2.99:1 (< 4.5:1); declare --on-accent-2 in its theme.css",
                "collection space-cargo-cannon (light): sub-nav link #5c5c57 on #1d7460 = "
                "1.19:1 (< 4.5:1); declare --on-accent-2-muted in its theme.css",
                "collection space-cargo-cannon (dark): collection title #e8e8e4 on #55c99f = "
                "1.67:1 (< 4.5:1); declare --on-accent-2 in its theme.css",
                "collection space-cargo-cannon (dark): sub-nav link #b4b4ae on #55c99f = "
                "1.02:1 (< 4.5:1); declare --on-accent-2-muted in its theme.css",
            ],
        )

    def test_declaring_the_triple_clears_the_gate(self):
        """The fix the downstream repo shipped is exactly what the gate asks for."""
        cascaded = contrast.cascade([contrast.scan_stylesheet(SATURATED_FIXED)])
        self.assertEqual(contrast.check_theme(cascaded, "collection space-cargo-cannon"), [])

    def test_the_stock_palette_passes_in_both_schemes(self):
        cascaded = contrast.cascade([contrast.scan_stylesheet(STOCK)])
        self.assertEqual(contrast.check_theme(cascaded, "site theme"), [])

    def test_the_house_theme_passes_in_both_schemes(self):
        bamboo = (REPO / "site" / "assets" / "theme-bamboo.css").read_text(encoding="utf-8")
        cascaded = contrast.cascade(
            [contrast.scan_stylesheet(STOCK), contrast.scan_stylesheet(bamboo)]
        )
        self.assertEqual(contrast.check_theme(cascaded, "site theme"), [])

    def test_unresolvable_tokens_are_skipped_rather_than_reported(self):
        """A theme that answers in `color-mix()` is doing something reasonable, not something wrong.

        Without this the gate would either guess (and be wrong) or fail every such build. Note the
        pairings here are *worse* than the failing case above - the point is that the checker says
        nothing because it cannot see them, not because they are fine.
        """
        css = SATURATED.replace(
            "--accent: #1d7460; --accent-2: #1d7460;",
            "--accent: #1d7460; --accent-2: #1d7460;"
            " --on-accent-2: color-mix(in srgb, #1d7460 10%, white);"
            " --on-accent-2-muted: oklch(62% 0.19 160);"
            " --on-accent-2-surface: var(--accent-2);",
        )
        cascaded = contrast.cascade([contrast.scan_stylesheet(css)])
        light_only = [w for w in contrast.check_theme(cascaded, "c") if "(light)" in w]
        self.assertEqual(light_only, [])

    def test_an_empty_or_unparseable_stylesheet_is_not_an_error(self):
        for text in ("", "   ", "@charset 'utf-8';", ":root { --accent-2: #123456;"):
            self.assertIsInstance(contrast.scan_stylesheet(text), dict)
        self.assertEqual(contrast.check_theme({}, "nothing"), [])


class PilotTests(unittest.TestCase):
    """The gate against this repository's own configuration - the pilot has to stay honest."""

    def test_the_gate_runs_over_the_pilot_and_returns_strings(self):
        from yss.build import _contrast_warnings
        from yss.config import Config

        warnings = _contrast_warnings(Config.load(REPO), set())
        self.assertTrue(all(isinstance(w, str) for w in warnings))
        # The site theme itself is clean; only a collection can be at fault.
        self.assertEqual([w for w in warnings if w.startswith("site theme")], [])


if __name__ == "__main__":
    unittest.main()

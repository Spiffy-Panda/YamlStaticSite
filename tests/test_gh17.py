"""Regression tests for gh-17: clicking a TOC entry or landing on a `#section` URL scrolled the
heading to y=0, where the sticky `.site-header` covers it - the stylesheet had no scroll offset of
any kind.

The fix is one token. `:root --header-offset` names the height the header occupies, `html` spends it
as `scroll-padding-top` (which covers `:target` jumps, in-page link clicks and `scrollIntoView`
alike, unlike a `scroll-margin-top` sprinkled over `[id]`), and the sticky `.toc` stops hard-coding
the same number and spends the token too. The coupling is the point: move the header and both
follow.

These assertions read the *built* `assets/yss.css`, not the source file, so a site that ships its
own stylesheet on top still has to carry the offset.

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

from test_features import TempSiteCase  # noqa: E402

TOKEN = "--header-offset"


def rule(css: str, selector: str) -> str:
    """The declaration block of the first top-level `selector { ... }` rule in `css`."""
    match = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", css)
    assert match, f"no rule for {selector!r}"
    return match.group(1)


class BuiltStylesheetTests(TempSiteCase):
    def css(self, target: str = "private") -> str:
        report = build(self.cfg, target, out_dir=self.root / f"out-{target}", run_dynamic=False)
        path = report.out_dir / "assets" / "yss.css"
        self.assertTrue(path.exists(), path)
        return path.read_text(encoding="utf-8")

    def test_root_defines_the_header_offset_token(self):
        declarations = rule(self.css(), ":root")
        self.assertRegex(declarations, re.escape(TOKEN) + r"\s*:\s*[\d.]+rem")

    def test_html_scroll_padding_top_uses_the_token(self):
        declarations = rule(self.css(), "html")
        self.assertRegex(declarations, r"scroll-padding-top\s*:\s*var\(\s*" + re.escape(TOKEN) + r"\s*\)")

    def test_toc_top_uses_the_token_rather_than_a_literal(self):
        declarations = rule(self.css(), ".toc")
        self.assertRegex(declarations, r"top\s*:\s*var\(\s*" + re.escape(TOKEN) + r"\s*\)")
        self.assertNotRegex(declarations, r"top\s*:\s*[\d.]+(rem|px|em)")

    def test_the_offset_is_at_least_the_header_min_height(self):
        """The header is `min-height: 3.25rem` plus its padding; an offset below that would still
        tuck a heading under it."""
        css = self.css()
        offset = float(re.search(re.escape(TOKEN) + r"\s*:\s*([\d.]+)rem", css).group(1))
        header = float(re.search(r"\.site-header \.wrap \{[^}]*min-height\s*:\s*([\d.]+)rem", css).group(1))
        self.assertGreaterEqual(offset, header)

    def test_the_public_build_ships_the_same_offset(self):
        self.assertIn("scroll-padding-top: var(" + TOKEN + ")", self.css("public"))

    def test_the_theme_stylesheet_only_overrides_tokens(self):
        """site.yaml's theme.css loads after the base sheet; if it declared layout properties it
        could undo the offset. It may only set custom properties."""
        theme = self.root / "site" / "assets" / "theme-bamboo.css"
        if not theme.exists():
            self.skipTest("no theme stylesheet")
        body = re.sub(r"/\*.*?\*/", "", theme.read_text(encoding="utf-8"), flags=re.S)
        declared = re.findall(r"(?m)^\s*([a-zA-Z-]+)\s*:", body)
        self.assertEqual([d for d in declared if not d.startswith("--")], [])


if __name__ == "__main__":
    unittest.main()

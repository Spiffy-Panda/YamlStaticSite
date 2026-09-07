"""Regression tests for gh-39: every prefab's css/js shipped to every target.

`prefab_css`/`prefab_js` iterated the whole registry, so `dist/public/assets/prefabs.css` carried
the styles of prefabs no public page renders - including ones reached only from a
`visibility: private` page. That is dead weight on every visitor, and a small leak besides: a
selector set is a description of markup, and the markup of a private page is private.

The fix records what `Renderer.prefab()` actually rendered. That method is the single choke point
every prefab passes through, nested `prefab('badge', ...)` calls from inside another prefab's
template included, so the second test below is the one that would catch a naive "scan the page
YAML for `prefab:` sections" implementation.

Run with: python -m unittest tests.test_gh39 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
# Also importable as `tests.test_gh__` (not only via `unittest discover -s tests`), so the
# sibling helper import below resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss.build import build  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

OUTER_CSS = "gh39-outer-marker { color: rebeccapurple; }"
OUTER_JS = "/* gh39-outer-script */"
INNER_CSS = "gh39-inner-marker { color: teal; }"
INNER_JS = "/* gh39-inner-script */"


class PerTargetPrefabBundleTests(TempSiteCase):
    def setUp(self):
        super().setUp()
        prefabs = self.root / "site" / "prefabs"
        prefabs.mkdir(parents=True, exist_ok=True)
        # `gh39-inner` is never named by a page; it is reached only from `gh39-outer`'s template.
        (prefabs / "gh39-inner.yaml").write_text(
            "name: gh39-inner\n"
            "description: Nested-only prefab used to prove the choke point records nested renders.\n"
            "category: layout\n"
            "params:\n"
            "  text: {type: string, default: inner}\n"
            "template: |\n"
            "  <span class=\"gh39-inner\">{{ text }}</span>\n"
            f"css: |\n  {INNER_CSS}\n"
            f"js: |\n  {INNER_JS}\n",
            encoding="utf-8",
        )
        (prefabs / "gh39-outer.yaml").write_text(
            "name: gh39-outer\n"
            "description: Private-only prefab whose template renders gh39-inner.\n"
            "category: layout\n"
            "params:\n"
            "  text: {type: string, default: outer}\n"
            "template: |\n"
            "  <div class=\"gh39-outer\">{{ text }}{{ prefab('gh39-inner') }}</div>\n"
            f"css: |\n  {OUTER_CSS}\n"
            f"js: |\n  {OUTER_JS}\n",
            encoding="utf-8",
        )
        (self.root / "site" / "pages" / "gh39.yaml").write_text(
            "id: gh39\n"
            "route: /gh39/\n"
            "title: GH39\n"
            "summary: A page that exists only in the private target.\n"
            "visibility: private\n"
            "sections:\n"
            "  - id: only\n"
            "    type: prefab\n"
            "    prefab: gh39-outer\n"
            "    args: {text: hello}\n",
            encoding="utf-8",
        )

    def bundles(self, target: str) -> tuple[str, str]:
        report = build(self.cfg, target, run_dynamic=False)
        assets = report.out_dir / "assets"
        self.assertTrue((assets / "prefabs.css").is_file(), "the file is written even when empty")
        self.assertTrue((assets / "prefabs.js").is_file())
        return (
            (assets / "prefabs.css").read_text(encoding="utf-8"),
            (assets / "prefabs.js").read_text(encoding="utf-8"),
        )

    def test_a_private_only_prefab_does_not_reach_the_public_bundle(self):
        css, js = self.bundles("public")
        self.assertNotIn(OUTER_CSS, css)
        self.assertNotIn(OUTER_JS, js)
        self.assertNotIn("/* prefab: gh39-outer */", css)

    def test_the_private_bundle_still_carries_it(self):
        css, js = self.bundles("private")
        self.assertIn(OUTER_CSS, css)
        self.assertIn(OUTER_JS, js)

    def test_a_prefab_reached_only_through_a_nested_call_is_bundled(self):
        """`gh39-inner` appears in no page YAML at all - only inside `gh39-outer`'s template."""
        css, js = self.bundles("private")
        self.assertIn(INNER_CSS, css)
        self.assertIn(INNER_JS, js)

    def test_the_public_bundle_is_not_empty_but_is_smaller(self):
        """The narrowing must be a filter, not an off switch: real public pages keep their css."""
        public_css, _ = self.bundles("public")
        private_css, _ = self.bundles("private")
        self.assertIn("/* prefab: ", public_css)
        self.assertLess(len(public_css), len(private_css))


if __name__ == "__main__":
    unittest.main()

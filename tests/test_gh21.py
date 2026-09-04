"""Regression tests for gh-21: `build --strict` deleted dist/<target> on every strict failure.

Six failure paths in `build_target` each did the same two lines - `shutil.rmtree(out)` then raise -
but only one of them had anything to contain. A forbidden string in `dist/` is material that must
not sit on disk where a later step could upload it, so removing the tree is the whole point. A
duplicate anchor id, a dead reference, a dead link, a flagged string or a stale evidence claim
produces output that is merely *wrong*, and deleting it removes the one artefact you need in order
to diagnose the failure.

The fix is not simply "drop the five rmtrees". Two of those gates fire *before* the assets, the
data export, the mounts and the leak scan, so raising there would leave a partial tree that
`scan_tree` never looked at - on the public target, exactly the tree the deletion exists to
prevent. So strict failures are collected, the build runs to completion, the leak scan keeps its
containment `rmtree`, and everything strict has to say is raised once at the end.

Covered here: a strict failure leaves a *complete* tree; a forbidden string still removes it;
a forbidden string wins even when strict failures are also pending; and several strict failures
are reported together rather than one at a time.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import BuildError, build  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class StrictFailureLeavesTheTreeTests(TempSiteCase):
    def write_page(self, name: str, body: str) -> Config:
        (self.root / "site" / "pages" / f"{name}.yaml").write_text(body, encoding="utf-8")
        return Config.load(self.root)

    def dead_link_page(self) -> Config:
        return self.write_page(
            "deadlink",
            "id: deadlink\nroute: /deadlink/\ntitle: Dead link\n"
            "sections: [{id: s, type: html, html: '<a href=\"/nope/missing.html\">dead link</a>'}]\n",
        )

    def test_dead_link_under_strict_leaves_a_complete_output_tree(self):
        """The artefact you need to diagnose the failure is the one the build had already produced."""
        cfg = self.dead_link_page()
        out = self.root / "dist" / "public"

        with self.assertRaises(BuildError) as caught:
            build(cfg, "public", run_dynamic=False, strict=True)
        message = str(caught.exception)
        self.assertIn("dead link", message)
        self.assertIn("left in place for inspection", message)

        self.assertTrue((out / "index.html").is_file(), "the rendered pages must survive")
        self.assertTrue((out / "deadlink" / "index.html").is_file(), "the offending page most of all")
        # The two early gates fire before these are written; a complete tree is what proves the
        # build ran past them rather than raising in the middle.
        self.assertTrue((out / "assets" / "yss.css").is_file(), "assets are emitted after the early gates")
        self.assertTrue((out / "data" / "docs.json").is_file(), "the data export is too")
        self.assertTrue((out / "build.json").is_file(), "and the manifest last of all")

    def test_the_same_build_without_strict_still_succeeds_with_a_warning(self):
        """Only the strictness changed, not the diagnosis."""
        cfg = self.dead_link_page()
        report = build(cfg, "public", run_dynamic=False, strict=False)
        self.assertTrue(any("dead link" in w for w in report.warnings), report.warnings)
        self.assertTrue((self.root / "dist" / "public" / "index.html").is_file())


class ContainmentIsKeptTests(TempSiteCase):
    def leak_page(self, extra: str = "") -> Config:
        (self.root / "site" / "pages" / "leak.yaml").write_text(
            "id: leak\nroute: /leak/\ntitle: Leak\n"
            f"sections: [{{id: s, type: html, html: 'contains SEKRIT here{extra}'}}]\n",
            encoding="utf-8")
        return Config.load(self.root)

    def test_a_forbidden_string_still_removes_the_tree(self):
        """The one failure with something to contain keeps containing it."""
        os.environ["YSS_FORBIDDEN_STRINGS"] = "SEKRIT"
        cfg = self.leak_page()
        with self.assertRaises(BuildError) as caught:
            build(cfg, "public", run_dynamic=False, strict=False)
        self.assertIn("forbidden string", str(caught.exception))
        self.assertIn("output removed", str(caught.exception))
        self.assertFalse((self.root / "dist" / "public").exists(), "a leak must not survive on disk")

    def test_a_forbidden_string_wins_over_a_pending_strict_failure(self):
        """A build that is both wrong and leaking is contained, not left for inspection."""
        os.environ["YSS_FORBIDDEN_STRINGS"] = "SEKRIT"
        cfg = self.leak_page(extra=' <a href=\"/nope/missing.html\">dead</a>')
        with self.assertRaises(BuildError) as caught:
            build(cfg, "public", run_dynamic=False, strict=True)
        self.assertIn("forbidden string", str(caught.exception))
        self.assertFalse((self.root / "dist" / "public").exists())

    def test_a_flagged_string_only_warns_and_keeps_its_tree(self):
        """Flagged strings warn (config.redaction_lists); under strict they fail without deleting."""
        os.environ["YSS_FLAG_STRINGS"] = "SEKRIT"
        cfg = self.leak_page()
        with self.assertRaises(BuildError) as caught:
            build(cfg, "public", run_dynamic=False, strict=True)
        self.assertIn("flagged strings present", str(caught.exception))
        self.assertTrue((self.root / "dist" / "public" / "leak" / "index.html").is_file())


class FailuresAreReportedTogetherTests(TempSiteCase):
    def test_two_strict_failures_are_raised_in_one_error(self):
        """Running to completion means the second gate is reachable, so both get reported."""
        os.environ["YSS_FLAG_STRINGS"] = "SEKRIT"
        (self.root / "site" / "pages" / "both.yaml").write_text(
            "id: both\nroute: /both/\ntitle: Both\n"
            "sections: [{id: s, type: html, html: '<a href=\"/nope/missing.html\">SEKRIT</a>'}]\n",
            encoding="utf-8")
        cfg = Config.load(self.root)
        with self.assertRaises(BuildError) as caught:
            build(cfg, "public", run_dynamic=False, strict=True)
        message = str(caught.exception)
        self.assertIn("dead link", message)
        self.assertIn("flagged strings present", message)
        self.assertTrue((self.root / "dist" / "public" / "both" / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()

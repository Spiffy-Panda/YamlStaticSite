"""Ruling tests for gh-22: how far may a mount's source path reach?

`_mount` validated the mount *destination* against the output root and had no counterpart check
for the *source*, so a collection could mount a folder outside its own root and it worked silently.
The schema said "Folder relative to the collection root", which reads as *within*, but nothing
enforced that reading - the behaviour existed by the absence of a check rather than by a decision.

adr-032 rules: a mount source **may** leave its collection and **may not** leave the site. The
escape is what a migration needs - a placeholder collection mounts another generator's output so
the strict build's dead-link gate sees a complete tree - and the site root is the boundary because
a mount's bytes get published, which is where `include` sections already draw the line.

These tests exist so that a future hardening pass that adds the symmetric collection-root check
fails loudly here instead of silently breaking every consumer relying on the affordance.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import BuildError, build  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class MountCase(TempSiteCase):
    def make_collection(self, mount_path: str) -> Config:
        coll = self.root / "examples" / "mounter"
        (coll / "docs").mkdir(parents=True)
        (coll / "pages").mkdir()
        (coll / "collection.yaml").write_text(
            "title: Mounter\n"
            "mounts:\n"
            f'  - path: "{mount_path}"\n'
            "    at: legacy/\n"
            "    targets: [private, public]\n",
            encoding="utf-8")
        (coll / "docs" / "plan.yaml").write_text(
            "kind: plan\nid: plan\ntitle: Mounter plan\nstatus: active\nmilestones: []\n", encoding="utf-8")
        (coll / "pages" / "index.yaml").write_text(
            "id: index\ntitle: Mounter\nsections: [{id: s, type: markdown, markdown: hello}]\n",
            encoding="utf-8")
        return Config.load(self.root)

    def make_source(self, relative_to_root: str) -> Path:
        folder = self.root / relative_to_root
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "legacy.html").write_text("<p>from another generator</p>", encoding="utf-8")
        return folder


class EscapingTheCollectionIsSupportedTests(MountCase):
    def test_a_source_outside_the_collection_but_inside_the_site_is_copied(self):
        """The migration shape: `../../<somewhere in the repo>` is a documented affordance."""
        self.make_source("legacy-site/musings/thing")
        cfg = self.make_collection("../../legacy-site/musings/thing")
        report = build(cfg, "private", run_dynamic=False)
        copied = self.root / "dist" / "private" / "mounter" / "legacy" / "legacy.html"
        self.assertTrue(copied.is_file(), report.warnings)

    def test_it_is_supported_on_the_public_target_too(self):
        """The consumer's eight collections carry this mount on public, not private."""
        self.make_source("legacy-site/musings/thing")
        cfg = self.make_collection("../../legacy-site/musings/thing")
        build(cfg, "public", run_dynamic=False)
        self.assertTrue((self.root / "dist" / "public" / "mounter" / "legacy" / "legacy.html").is_file())

    def test_the_schema_says_so_rather_than_leaving_it_to_be_discovered(self):
        """A ruling nobody can read is the state this issue was filed about."""
        text = (REPO / "yss" / "schemas" / "collection.schema.yaml").read_text(encoding="utf-8")
        self.assertIn("may**\n            point outside the collection", text.replace("\r\n", "\n"))
        self.assertIn("adr-032", text)


class EscapingTheSiteIsRefusedTests(MountCase):
    def test_a_source_above_the_site_root_fails_the_build(self):
        cfg = self.make_collection("../../../outside")
        (self.root.parent / "outside").mkdir(exist_ok=True)
        try:
            with self.assertRaises(BuildError) as caught:
                build(cfg, "private", run_dynamic=False)
        finally:
            (self.root.parent / "outside").rmdir()
        message = str(caught.exception)
        self.assertIn("escapes the site root", message)
        self.assertIn("outside its collection but not outside the site", message)

    def test_the_rule_matches_the_one_include_sections_already_enforce(self):
        """Two mechanisms that publish bytes from a path should agree on the boundary."""
        render = (REPO / "yss" / "render.py").read_text(encoding="utf-8")
        build_py = (REPO / "yss" / "build.py").read_text(encoding="utf-8")
        self.assertIn("escapes the site root", render)
        self.assertIn("escapes the site root", build_py)

    def test_the_destination_check_is_untouched(self):
        """The `at:` guard was always right and is not what this issue questioned."""
        coll = self.root / "examples" / "mounter"
        self.make_collection(".")
        (coll / "collection.yaml").write_text(
            'title: Mounter\nmounts:\n  - path: "."\n    at: "../../escape/"\n    targets: [private]\n',
            encoding="utf-8")
        with self.assertRaises(BuildError) as caught:
            build(Config.load(self.root), "private", run_dynamic=False)
        self.assertIn("escapes the output directory", str(caught.exception))


class ShippedExampleTests(TempSiteCase):
    def test_the_demo_collections_own_mount_still_works(self):
        """`path: play` is the ordinary within-the-collection case and must be unaffected."""
        build(self.cfg, "private", run_dynamic=False)
        self.assertTrue((self.root / "dist" / "private" / "demo-musing" / "play" / "index.html").is_file())


if __name__ == "__main__":
    unittest.main()

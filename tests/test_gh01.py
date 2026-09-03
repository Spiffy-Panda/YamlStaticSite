"""Regression tests for gh-1: `yss new collection <id>`.

Every test that scaffolds does so in a temporary copy of the pilot site, never in the real repo.
"""
from __future__ import annotations

import re
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.cli import main  # noqa: E402
from yss.config import Config, ConfigError  # noqa: E402
from yss.loader import SchemaRegistry  # noqa: E402
from yss.scaffold import ScaffoldError, new_collection  # noqa: E402


def temp_site() -> Path:
    """Copy the pilot site's sources (not dist, not .yss) into a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-gh01-"))
    for name in ("site.yaml", "README.md"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, tmp / name)
    for sub in ("docs", "site", "schemas", "examples"):
        if (REPO / sub).is_dir():
            shutil.copytree(REPO / sub, tmp / sub)
    shutil.copytree(REPO / "tests", tmp / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "yss", tmp / "yss", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "dist").mkdir()
    return tmp


class TempSiteCase(unittest.TestCase):
    def setUp(self):
        self.root = temp_site()
        self.cfg = Config.load(self.root)

    def tearDown(self):
        shutil.rmtree(self.root, ignore_errors=True)


class NewCollectionCliTests(TempSiteCase):
    def test_scaffolds_and_validates(self):
        self.assertEqual(
            main(["--root", str(self.root), "new", "collection", "game-test", "--title", "Game Test"]),
            0,
        )
        dest = self.root / "examples" / "game-test"
        self.assertTrue((dest / "collection.yaml").is_file())
        self.assertTrue((dest / "docs" / "plan.yaml").is_file())
        self.assertTrue((dest / "pages" / "index.yaml").is_file())

        cfg = Config.load(self.root)
        ids = [c.id for c in cfg.collections()]
        self.assertIn("game-test", ids)
        collection = cfg.collection("game-test")
        self.assertEqual(collection.title, "Game Test")

        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("game-test/plan", loaded.docs)
        page = next(p for p in loaded.pages if p["id"] == "game-test/index")
        self.assertEqual(page["route"], "/game-test/")

    def test_refuses_to_overwrite_without_force(self):
        self.assertEqual(main(["--root", str(self.root), "new", "collection", "game-test"]), 0)
        self.assertEqual(main(["--root", str(self.root), "new", "collection", "game-test"]), 1)
        self.assertEqual(main(["--root", str(self.root), "new", "collection", "game-test", "--force"]), 0)

    def test_build_succeeds_with_new_collection(self):
        self.assertEqual(main(["--root", str(self.root), "new", "collection", "game-test"]), 0)
        self.assertEqual(main(["--root", str(self.root), "build", "--target", "all", "--no-dynamic"]), 0)
        self.assertTrue((self.root / "dist" / "private" / "game-test" / "index.html").exists())
        self.assertTrue((self.root / "dist" / "public" / "game-test" / "index.html").exists())


class NewCollectionRootResolutionTests(TempSiteCase):
    def test_errors_without_any_collections_root(self):
        site_path = self.root / "site.yaml"
        text = site_path.read_text(encoding="utf-8")
        text = re.sub(r"(?m)^collections:\n(?:  - .*\n)+", "", text)
        site_path.write_text(text, encoding="utf-8")
        cfg = Config.load(self.root)
        reg = SchemaRegistry(cfg.schema_dirs())
        with self.assertRaises(ScaffoldError):
            new_collection(cfg, reg, "anything")

    def test_errors_when_root_is_ambiguous_and_resolves_with_flag(self):
        site_path = self.root / "site.yaml"
        text = site_path.read_text(encoding="utf-8")
        (self.root / "musings").mkdir()
        text = re.sub(r"(?m)^(collections:\n(?:  - .*\n)+)", r"\1  - root: musings/*\n", text)
        site_path.write_text(text, encoding="utf-8")
        cfg = Config.load(self.root)
        reg = SchemaRegistry(cfg.schema_dirs())
        with self.assertRaises(ScaffoldError):
            new_collection(cfg, reg, "ambiguous")
        # disambiguated with --root, scaffolds into the chosen root
        paths = new_collection(cfg, reg, "picked", root="musings/*")
        self.assertTrue((self.root / "musings" / "picked" / "collection.yaml").is_file())
        for path in paths:
            self.assertTrue(path.is_file())


if __name__ == "__main__":
    unittest.main()

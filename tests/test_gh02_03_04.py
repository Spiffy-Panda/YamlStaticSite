"""Regression tests for gh-2 (collection id override), gh-3 (mount include/exclude) and gh-4
(collection route prefix). Run with: python -m unittest discover -s tests -v

Every test builds in a temporary copy of the pilot site, never in dist/ (see TempSiteCase).
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402
from yss.config import Config, ConfigError  # noqa: E402


def temp_site() -> Path:
    """Copy the pilot site's sources (not dist, not .yss) into a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-gh-"))
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
        self._env = dict(os.environ)
        os.environ.pop("YSS_FORBIDDEN_STRINGS", None)
        os.environ.pop("YSS_FLAG_STRINGS", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.root, ignore_errors=True)

    # --- fixture helpers ---------------------------------------------------
    def make_collection(self, folder: Path, collection_yaml: str, doc: str | None = None, page: str | None = None) -> None:
        folder.mkdir(parents=True, exist_ok=True)
        (folder / "collection.yaml").write_text(collection_yaml, encoding="utf-8")
        if doc is not None:
            (folder / "docs").mkdir(exist_ok=True)
            (folder / "docs" / "note.yaml").write_text(doc, encoding="utf-8")
        if page is not None:
            (folder / "pages").mkdir(exist_ok=True)
            (folder / "pages" / "index.yaml").write_text(page, encoding="utf-8")

    def add_collections_entry(self, entry: dict) -> Config:
        """Append a group to site.yaml's `collections:` list (preserving the existing ones) and reload."""
        path = self.root / "site.yaml"
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        data.setdefault("collections", []).append(entry)
        path.write_text(yaml.safe_dump(data, sort_keys=False), encoding="utf-8")
        return Config.load(self.root)

    MINIMAL_DOC = "kind: generic\ntitle: Note\ndata: {x: 1}\n"
    MINIMAL_PAGE = "title: Home\nsections:\n  - {id: s, type: markdown, markdown: hi}\n"


# --- gh-2: collection id override -------------------------------------------
class CollectionIdOverrideTests(TempSiteCase):
    def test_id_override_changes_route_and_doc_prefix(self):
        self.make_collection(
            self.root / "examples" / "Weird-Case-Folder",
            "title: Weird\nid: weird-slug\n",
            doc=self.MINIMAL_DOC,
            page=self.MINIMAL_PAGE,
        )
        cfg = Config.load(self.root)
        collection = cfg.collection("weird-slug")
        self.assertEqual(collection.route_prefix, "/weird-slug/")

        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("weird-slug/note", loaded.docs)
        self.assertNotIn("Weird-Case-Folder/note", loaded.docs)
        self.assertNotIn("weird-case-folder/note", loaded.docs)
        page = next(p for p in loaded.pages if p["_collection"] == "weird-slug")
        self.assertEqual(page["route"], "/weird-slug/")

        rep = build(cfg, "private", run_dynamic=False, loaded=loaded)
        self.assertTrue((rep.out_dir / "weird-slug" / "index.html").exists())
        self.assertFalse((rep.out_dir / "Weird-Case-Folder").exists())
        self.assertFalse((rep.out_dir / "weird-case-folder").exists())

    def test_invalid_id_override_fails_clearly(self):
        self.make_collection(self.root / "examples" / "bad-id-folder", "title: Bad\nid: 'Not A Slug!'\n")
        cfg = Config.load(self.root)
        with self.assertRaises(ConfigError) as caught:
            cfg.collections()
        self.assertIn("not a valid slug", str(caught.exception))

    def test_duplicate_id_override_fails_naming_both_folders(self):
        self.make_collection(self.root / "examples" / "dup-a", "title: A\nid: shared-slug\n")
        self.make_collection(self.root / "examples" / "dup-b", "title: B\nid: shared-slug\n")
        cfg = Config.load(self.root)
        with self.assertRaises(ConfigError) as caught:
            cfg.collections()
        message = str(caught.exception)
        self.assertIn("shared-slug", message)
        self.assertIn("dup-a", message)
        self.assertIn("dup-b", message)


# --- gh-3: mount include/exclude --------------------------------------------
class MountFilterTests(TempSiteCase):
    def setUp(self):
        super().setUp()
        collection = self.root / "examples" / "mount-filter-test"
        self.make_collection(
            collection,
            "title: Mount filter test\n"
            "mounts:\n"
            "  - path: stuff\n"
            "    at: stuff/\n"
            "    targets: [private]\n"
            "    include: [\"*.html\"]\n"
            "    exclude: [\"secret.html\"]\n"
            "  - path: stuff2\n"
            "    at: stuff2/\n"
            "    targets: [private]\n"
            "    include: [\"**/*.html\"]\n"
            "    exclude: [\"priv/\"]\n",
        )
        stuff = collection / "stuff"
        (stuff / "sub").mkdir(parents=True)
        (stuff / "a.html").write_text("a", encoding="utf-8")
        (stuff / "b.html").write_text("b", encoding="utf-8")
        (stuff / "secret.html").write_text("secret", encoding="utf-8")
        (stuff / "notes.txt").write_text("notes", encoding="utf-8")
        (stuff / "sub" / "c.html").write_text("c", encoding="utf-8")
        stuff2 = collection / "stuff2"
        (stuff2 / "priv").mkdir(parents=True)
        (stuff2 / "pub.html").write_text("pub", encoding="utf-8")
        (stuff2 / "priv" / "x.html").write_text("x", encoding="utf-8")
        self.cfg = Config.load(self.root)

    def test_include_is_not_recursive_and_exclude_wins(self):
        rep = build(self.cfg, "private", run_dynamic=False)
        out = rep.out_dir / "mount-filter-test" / "stuff"
        self.assertTrue((out / "a.html").exists())
        self.assertTrue((out / "b.html").exists())
        self.assertFalse((out / "secret.html").exists())   # excluded
        self.assertFalse((out / "notes.txt").exists())     # not matched by include
        self.assertFalse((out / "sub" / "c.html").exists())  # "*.html" is top-level only

    def test_recursive_include_and_directory_exclude(self):
        rep = build(self.cfg, "private", run_dynamic=False)
        out = rep.out_dir / "mount-filter-test" / "stuff2"
        self.assertTrue((out / "pub.html").exists())
        self.assertFalse((out / "priv" / "x.html").exists())  # whole "priv/" subtree excluded

    def test_no_filters_still_copies_the_whole_tree(self):
        """Existing mounts (no include/exclude) behave exactly as before."""
        rep = build(self.cfg, "private", run_dynamic=False)
        # the demo collection's mount has no include/exclude
        self.assertTrue((rep.out_dir / "demo-musing" / "play" / "index.html").exists())


# --- gh-4: collection route prefix ------------------------------------------
class CollectionRoutePrefixTests(TempSiteCase):
    def test_prefix_moves_pages_and_mounts_and_leaves_base_url_intact(self):
        collection = self.root / "examples-at" / "scc"
        self.make_collection(
            collection,
            "title: Space Cargo Cannon\n"
            "mounts:\n"
            "  - path: extra\n"
            "    at: extra/\n"
            "    targets: [private]\n",
            doc=self.MINIMAL_DOC,
            page=self.MINIMAL_PAGE,
        )
        (collection / "extra").mkdir()
        (collection / "extra" / "file.txt").write_text("hi", encoding="utf-8")

        cfg = self.add_collections_entry({"root": "examples-at/*", "at": "musings/"})
        collection_obj = cfg.collection("scc")
        self.assertEqual(collection_obj.route_prefix, "/musings/scc/")

        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        page = next(p for p in loaded.pages if p["_collection"] == "scc")
        self.assertEqual(page["route"], "/musings/scc/")
        self.assertEqual(page["id"], "scc/index")          # doc/page ids stay collection-id based
        self.assertIn("scc/note", loaded.docs)              # doc ids are unaffected by `at`

        rep = build(cfg, "private", run_dynamic=False, loaded=loaded)
        self.assertTrue((rep.out_dir / "musings" / "scc" / "index.html").exists())
        self.assertTrue((rep.out_dir / "musings" / "scc" / "extra" / "file.txt").exists())
        self.assertFalse((rep.out_dir / "scc").exists())    # not also at the unprefixed location

        # base_url composition for the public target is untouched by collection prefixes
        self.assertEqual(cfg.base_url("public"), "/YamlStaticSite/")
        pub = build(cfg, "public", run_dynamic=False, loaded=loaded)
        html = (pub.out_dir / "musings" / "scc" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/YamlStaticSite/', html)

    def test_collection_without_new_fields_is_unchanged(self):
        """A collection using none of id/at/include/exclude behaves exactly as before (gh-2/3/4)."""
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("demo-musing/plan", loaded.docs)
        page = next(p for p in loaded.pages if p["id"] == "demo-musing/index")
        self.assertEqual(page["route"], "/demo-musing/")
        rep = build(self.cfg, "private", run_dynamic=False, loaded=loaded)
        self.assertTrue((rep.out_dir / "demo-musing" / "index.html").exists())
        self.assertTrue((rep.out_dir / "demo-musing" / "play" / "index.html").exists())


if __name__ == "__main__":
    unittest.main()

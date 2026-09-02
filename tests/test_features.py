"""Tests for evidence, annotations, references, collections, hooks, archives and worksheets.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import BuildError, build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.evidence import check  # noqa: E402
from yss.render import Renderer  # noqa: E402


def temp_site() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="yss-feat-"))
    for name in ("site.yaml", "README.md"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, tmp / name)
    for sub in ("docs", "site", "schemas", "examples"):
        if (REPO / sub).is_dir():
            shutil.copytree(REPO / sub, tmp / sub)
    # evidence in the pilot docs cites yss/, tests/ and dist/; mirror them so the claims hold
    shutil.copytree(REPO / "tests", tmp / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "yss", tmp / "yss", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "dist").mkdir()
    (tmp / ".claude" / "skills").mkdir(parents=True)
    (tmp / ".github" / "workflows").mkdir(parents=True)
    shutil.copy(REPO / ".github" / "workflows" / "pages.yml", tmp / ".github" / "workflows" / "pages.yml")
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

    def write_doc(self, name: str, text: str) -> None:
        (self.root / "docs" / name).write_text(text, encoding="utf-8")


class AnnotationTests(TempSiteCase):
    def test_vocab_resolved_from_config(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.write_doc("v.yaml", "kind: plan\ntitle: V\nmilestones:\n  - {id: a, title: A, status: draft}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("milestones/0/status" in e and "planned" in e for e in loaded.errors), loaded.errors)

    def test_collection_overrides_vocabulary(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        demo = loaded.docs["demo-musing/plan"]
        self.assertEqual(demo["risks"][0]["status"], "watching")  # only valid in the collection's risk_status
        self.write_doc("r.yaml", "kind: plan\ntitle: R\nmilestones: []\nrisks:\n  - {id: r, title: R, status: watching}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("docs/r.yaml" in e and "risks/0/status" in e for e in loaded.errors), loaded.errors)

    def test_length_limit(self):
        self.write_doc("long.yaml", "kind: generic\ntitle: L\nsummary: " + "x" * 400 + "\ndata: {}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("docs/long.yaml" in e and "summary" in e and "too long" in e for e in loaded.errors), loaded.errors)

    def test_draft_status_rejected(self):
        self.write_doc("d.yaml", "kind: generic\ntitle: D\nstatus: draft\ndata: {}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("docs/d.yaml" in e and "status" in e for e in loaded.errors), loaded.errors)


class ReferenceTests(TempSiteCase):
    def test_dangling_depends_on(self):
        self.write_doc("x.yaml", "kind: plan\ntitle: X\nmilestones:\n  - {id: a, title: A, status: planned, depends_on: [nope]}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("docs/x.yaml" in e and "no item 'nope'" in e for e in loaded.errors), loaded.errors)

    def test_cross_doc_item_reference(self):
        self.write_doc("x.yaml", "kind: plan\ntitle: X\nmilestones:\n  - {id: a, title: A, status: planned, depends_on: [plan/m3-pilot]}\n  - {id: b, title: B, status: planned, depends_on: [plan/missing]}\n")
        loaded = load_all(self.cfg)
        self.assertFalse(any("milestones/0/depends_on" in e for e in loaded.errors), loaded.errors)
        self.assertTrue(any("milestones/1/depends_on" in e and "no item 'missing'" in e for e in loaded.errors), loaded.errors)

    def test_duplicate_item_id(self):
        self.write_doc("x.yaml", "kind: plan\ntitle: X\nmilestones:\n  - {id: a, title: A, status: planned}\n  - {id: a, title: B, status: planned}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("duplicate item id 'a'" in e for e in loaded.errors), loaded.errors)

    def test_inline_ref_validated_and_rendered(self):
        self.write_doc("x.yaml", "kind: generic\ntitle: X\nsummary: see [[plan#nope]]\ndata: {}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("inline reference [[plan#nope]]" in e for e in loaded.errors), loaded.errors)
        (self.root / "docs" / "x.yaml").unlink()
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = str(renderer.md("Read [[plan#m3-pilot]] and [[design]] and [[plan#m3-pilot|the pilot]]."))
        self.assertIn('href="/plan/#m3-pilot"', html)
        self.assertIn(">Pilot on this repository<", html)
        self.assertIn(">the pilot<", html)
        self.assertIn('href="/design/"', html)


class EvidenceTests(TempSiteCase):
    def test_pilot_docs_have_no_stale_claims(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        report = check(self.cfg, loaded.docs, loaded.registry, run_commands=False, git_recency=False)
        self.assertTrue(report.claims)
        self.assertEqual([c.target for c in report.stale], [])

    def test_missing_path_is_stale_and_injected(self):
        self.write_doc("x.yaml", "kind: codemap\ntitle: X\nmodules:\n  - {id: gone, path: yss/gone.py, purpose: nothing}\n  - {id: here, path: yss/cli.py, purpose: cli}\n")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        report = check(self.cfg, loaded.docs, loaded.registry, run_commands=False, git_recency=False)
        stale = [c for c in report.stale if c.doc == "x"]
        self.assertEqual(len(stale), 1)
        self.assertEqual(stale[0].item, "gone")
        rep = build(self.cfg, "private", run_dynamic=False, loaded=loaded)
        self.assertTrue(any("yss/gone.py" in w for w in rep.warnings))
        html = (rep.out_dir / "codemap" / "index.html").read_text(encoding="utf-8")
        self.assertNotIn("gone.py", html)  # x is not presented on the codemap page
        data = (rep.out_dir / "data" / "docs" / "x.json").read_text(encoding="utf-8")
        self.assertIn('"status": "stale"', data)
        with self.assertRaises(BuildError):
            build(self.cfg, "private", run_dynamic=False, loaded=loaded, strict=True)

    def test_explicit_claims(self):
        self.write_doc(
            "x.yaml",
            "kind: generic\ntitle: X\ndata: {}\nevidence:\n"
            "  - {path: yss/cli.py, contains: 'def main('}\n"
            "  - {path: yss/cli.py, contains: 'no such text'}\n"
            "  - {glob: 'yss/prefabs/*.yaml', min: 5}\n"
            "  - {symbol: 'yss.build:build'}\n"
            "  - {symbol: 'yss.build:nothing'}\n"
            "  - {command: 'python -c \"import sys; sys.exit(0)\"'}\n",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        report = check(self.cfg, {"x": loaded.docs["x"]}, loaded.registry, run_commands=False, git_recency=False)
        by = {c.field: c.status for c in report.claims}
        self.assertEqual(by["evidence/0"], "ok")
        self.assertEqual(by["evidence/1"], "stale")
        self.assertEqual(by["evidence/2"], "ok")
        self.assertEqual(by["evidence/3"], "ok")
        self.assertEqual(by["evidence/4"], "stale")
        self.assertEqual(by["evidence/5"], "skipped")
        report = check(self.cfg, {"x": loaded.docs["x"]}, loaded.registry, run_commands=True, git_recency=False)
        self.assertEqual({c.field: c.status for c in report.claims}["evidence/5"], "ok")


class CollectionTests(TempSiteCase):
    def test_demo_collection_loads_with_hooks(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("demo-musing/plan", loaded.docs)
        self.assertIn("demo-musing/inventory", loaded.docs)  # generated by hooks.load_docs
        demo = self.cfg.collection("demo-musing")
        self.assertIn("hooked", demo.data["tags"])  # configure() hook ran
        page = next(p for p in loaded.pages if p["id"] == "demo-musing/index")
        self.assertEqual(page["route"], "/demo-musing/")
        self.assertEqual(page["nav"]["group"], "Demo musing")

    def test_build_collection_outputs(self):
        loaded = load_all(self.cfg)
        priv = build(self.cfg, "private", run_dynamic=False, loaded=loaded)
        pub = build(self.cfg, "public", run_dynamic=False, loaded=loaded)
        self.assertTrue((priv.out_dir / "demo-musing" / "index.html").exists())
        self.assertTrue((priv.out_dir / "demo-musing" / "play" / "index.html").exists())  # mount, private only
        self.assertFalse((pub.out_dir / "demo-musing" / "play").exists())
        self.assertTrue((priv.out_dir / "demo-musing" / "generated-by-hook.txt").exists())  # after_build
        self.assertTrue((pub.out_dir / "demo-musing" / "assets" / "theme.css").exists())
        html = (pub.out_dir / "demo-musing" / "index.html").read_text(encoding="utf-8")
        self.assertIn("--accent:#7a3e9d", html)
        self.assertIn("collection-bar", html)
        self.assertIn("watching", html)  # local vocabulary rendered
        self.assertIn("play/index.html", html)  # generated doc bound as `inventory`
        self.assertIn("demo-musing/inventory.json", html)
        self.assertIn('href="/YamlStaticSite/plan/#m6-prototypes"', html)  # [[/plan#...]] root reference
        root_index = (pub.out_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="collection-demo-musing"', root_index)

    def test_private_collection_absent_from_public(self):
        (self.root / "examples" / "demo-musing" / "collection.yaml").write_text(
            "title: Demo\nvisibility: private\nvocabularies: {risk_status: [open, watching, resolved]}\n"
            "dynamic: {sources: {notes: {provider: hooks:notes}}}\n", encoding="utf-8"
        )
        cfg = Config.load(self.root)
        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        pub = build(cfg, "public", run_dynamic=False, loaded=loaded)
        self.assertFalse((pub.out_dir / "demo-musing").exists())
        self.assertNotIn("demo-musing", pub.docs)

    def test_hook_provider_dynamic_source(self):
        from yss.dynamic import collect_envelope

        env = collect_envelope(self.cfg, "demo-musing.notes", self.cfg.dynamic_sources["demo-musing.notes"])
        self.assertTrue(env["ok"], env)
        self.assertTrue(any(f["name"] == "index.html" for f in env["data"]["files"]))


class ArchiveTests(TempSiteCase):
    def test_underscore_paths_are_not_loaded(self):
        (self.root / "docs" / "_scratch.yaml").write_text("not: valid doc\n", encoding="utf-8")
        (self.root / "docs" / "_old").mkdir()
        (self.root / "docs" / "_old" / "plan.yaml").write_text("kind: plan\ntitle: Old\nmilestones: []\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertNotIn("_scratch", loaded.docs)
        self.assertNotIn("_old/plan", loaded.docs)
        self.assertTrue((REPO / "docs" / "_archive").is_dir())


class WorksheetTests(TempSiteCase):
    def test_worksheet_renders_with_defaults_and_config(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        rep = build(self.cfg, "public", run_dynamic=False, loaded=loaded)
        html = (rep.out_dir / "verdicts" / "index.html").read_text(encoding="utf-8")
        self.assertIn('data-q="v-adoption"', html)
        self.assertIn('value="collections" data-prompt="Accept adr-010', html)
        self.assertIn("checked", html)  # recommended default pre-selected
        self.assertIn("Build the instruction", html)
        self.assertIn('class="ws-config"', html)
        self.assertIn("RULES", (rep.out_dir / "assets" / "prefabs.js").read_text(encoding="utf-8"))
        pending = (rep.out_dir / "pending" / "index.html").read_text(encoding="utf-8")
        self.assertIn('class="ws-procon"', pending)      # pros/cons foldout per option
        self.assertIn('class="ws-compare"', pending)     # compare-all foldout per question
        self.assertIn('href="/YamlStaticSite/plan/#m6-godot"', pending)  # blocks link resolved
        self.assertIn("Blocked work waiting on this worksheet", pending)

    def test_site_theme_css_is_linked_after_the_base_stylesheet(self):
        """site.yaml theme.css rides on top of yss.css; it must not replace it."""
        rep = build(self.cfg, "public", run_dynamic=False)
        html = (rep.out_dir / "index.html").read_text(encoding="utf-8")
        base, theme = html.index("assets/yss.css"), html.index("assets/theme-bamboo.css")
        self.assertLess(base, theme, "theme stylesheet must be linked after the base one")
        self.assertTrue((rep.out_dir / "assets" / "yss.css").exists())
        css = (rep.out_dir / "assets" / "theme-bamboo.css").read_text(encoding="utf-8")
        self.assertIn("--accent", css)
        self.assertNotIn(".site-header", css)  # tokens only, no layout

    def test_playground_binds_only_the_shipped_presets(self):
        rep = build(self.cfg, "public", run_dynamic=False)
        html = (rep.out_dir / "themes" / "index.html").read_text(encoding="utf-8")
        for preset in ("ink-blue", "high-contrast", "lavender-warm", "trans-pride"):
            self.assertIn(f'data-pal="{preset}"', html)
        self.assertIn("lab-contrast", html)


if __name__ == "__main__":
    unittest.main()

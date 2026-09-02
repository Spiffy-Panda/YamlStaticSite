"""Tests for the yss toolchain, run with: python -m unittest discover -s tests -v

Every test that builds does so in a temporary copy of the pilot site, never in dist/.
"""
from __future__ import annotations

import http.client
import json
import os
import shutil
import sys
import tempfile
import threading
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.binding import BindError, resolve_binding  # noqa: E402
from yss.build import BuildError, build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.render import Renderer  # noqa: E402
from yss.visibility import filter_for_target, scan_text  # noqa: E402


def temp_site() -> Path:
    """Copy the pilot site's sources (not dist, not .yss) into a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-test-"))
    for name in ("site.yaml", "README.md"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, tmp / name)
    for sub in ("docs", "site", "schemas"):
        if (REPO / sub).is_dir():
            shutil.copytree(REPO / sub, tmp / sub)
    (tmp / "tests").mkdir()
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


class ValidationTests(TempSiteCase):
    def test_pilot_sources_validate(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("plan", loaded.docs)
        self.assertTrue(any(p["id"] == "index" for p in loaded.pages))
        self.assertIn("card-grid", loaded.prefabs)
        self.assertIn("prefab-gallery", loaded.prefabs)
        self.assertFalse(loaded.prefabs["prefab-gallery"]["_builtin"])

    def test_invalid_doc_reports_path(self):
        (self.root / "docs" / "broken.yaml").write_text(
            "kind: plan\ntitle: Broken\nmilestones:\n  - id: x\n    title: X\n    status: not-a-status\n", encoding="utf-8"
        )
        loaded = load_all(self.cfg)
        self.assertTrue(any("docs/broken.yaml" in e and "milestones/0/status" in e for e in loaded.errors), loaded.errors)

    def test_unknown_kind_reports_known_kinds(self):
        (self.root / "docs" / "odd.yaml").write_text("kind: recipe\ntitle: Odd\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        self.assertTrue(any("unknown doc kind 'recipe'" in e and "plan" in e for e in loaded.errors), loaded.errors)

    def test_page_with_unknown_prefab_fails_cross_check(self):
        (self.root / "site" / "pages" / "bad.yaml").write_text(
            "title: Bad\nsections:\n  - type: prefab\n    prefab: nope\n", encoding="utf-8"
        )
        loaded = load_all(self.cfg)
        self.assertTrue(any("unknown prefab 'nope'" in e for e in loaded.errors), loaded.errors)


class VisibilityTests(unittest.TestCase):
    def test_private_objects_and_notes_removed_for_public(self):
        data = {
            "title": "t",
            "private_notes": "secret",
            "items": [
                {"id": "a", "visibility": "public"},
                {"id": "b", "visibility": "private"},
                {"id": "c", "nested": {"visibility": "private", "x": 1}, "keep": {"y": 2}},
            ],
        }
        pub = filter_for_target(data, "public")
        self.assertNotIn("private_notes", pub)
        self.assertEqual([i["id"] for i in pub["items"]], ["a", "c"])
        self.assertNotIn("nested", pub["items"][1])
        self.assertEqual(pub["items"][1]["keep"], {"y": 2})
        priv = filter_for_target(data, "private")
        self.assertEqual(priv, data)

    def test_scan_text_is_case_insensitive(self):
        hits = scan_text("line one\nSecret Name here\nsecret NAME again", ["secret name"])
        self.assertEqual([h[1] for h in hits], [2, 3])


class BindingTests(unittest.TestCase):
    def setUp(self):
        self.ctx = {
            "docs": {
                "plan": {
                    "id": "plan",
                    "milestones": [
                        {"id": "m1", "title": "One", "status": "done", "priority": 2, "tags": ["x"]},
                        {"id": "m2", "title": "Two", "status": "active", "priority": 1},
                        {"id": "m3", "title": "Three", "status": "planned", "priority": 3, "tags": ["y"]},
                    ],
                }
            },
            "pages": [],
            "site": {"name": "s"},
            "all_doc_ids": ["plan", "secret"],
        }

    def test_where_sort_limit_fields(self):
        out = resolve_binding(
            {"from": "plan.milestones", "where": {"status": ["active", "planned"]}, "sort": "-priority", "limit": 1, "fields": ["id"]},
            self.ctx,
        )
        self.assertEqual(out, [{"id": "m3"}])

    def test_where_operators(self):
        items = resolve_binding({"from": "plan.milestones", "where": {"status": {"not": "done"}, "priority": {"lte": 1}}}, self.ctx)
        self.assertEqual([i["id"] for i in items], ["m2"])
        items = resolve_binding({"from": "plan.milestones", "where": {"tags": {"contains": "y"}}}, self.ctx)
        self.assertEqual([i["id"] for i in items], ["m3"])
        items = resolve_binding({"from": "plan.milestones", "where": {"tags": {"exists": False}}}, self.ctx)
        self.assertEqual([i["id"] for i in items], ["m2"])

    def test_map_and_group(self):
        render = lambda s, c: s.replace("{{ id }}", c["id"])  # noqa: E731 - minimal stand-in for Jinja
        items = resolve_binding({"from": "plan.milestones", "map": {"badge": "status", "href": "#{{ id }}", "fixed": 3}}, self.ctx, render)
        self.assertEqual(items[0]["badge"], "done")
        self.assertEqual(items[1]["href"], "#m2")
        self.assertEqual(items[2]["fixed"], 3)
        groups = resolve_binding({"from": "plan.milestones", "group_by": "status"}, self.ctx)
        self.assertEqual([g["key"] for g in groups], ["done", "active", "planned"])

    def test_errors_are_descriptive(self):
        with self.assertRaises(BindError) as cm:
            resolve_binding({"from": "secret.items"}, self.ctx)
        self.assertIn("private in this target", str(cm.exception))
        with self.assertRaises(BindError) as cm:
            resolve_binding({"from": "plan.nothing"}, self.ctx)
        self.assertIn("no field 'nothing'", str(cm.exception))
        with self.assertRaises(BindError):
            resolve_binding({"from": "plan.milestones", "bogus": 1}, self.ctx)


class BuildTests(TempSiteCase):
    def test_public_and_private_builds(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        priv = build(self.cfg, "private", run_dynamic=False, loaded=loaded)
        pub = build(self.cfg, "public", run_dynamic=False, loaded=loaded)
        self.assertIn("/", priv.pages)
        self.assertIn("/plan/", pub.pages)
        priv_index = (priv.out_dir / "index.html").read_text(encoding="utf-8")
        pub_index = (pub.out_dir / "index.html").read_text(encoding="utf-8")
        self.assertIn('id="local"', priv_index)  # private-only section
        self.assertNotIn('id="local"', pub_index)
        self.assertIn('href="/YamlStaticSite/plan/"', pub_index)
        self.assertIn('href="/plan/"', priv_index)
        self.assertTrue((pub.out_dir / ".nojekyll").exists())
        self.assertTrue((pub.out_dir / "data" / "docs" / "plan.json").exists())
        self.assertTrue((pub.out_dir / "schemas" / "doc.plan.json").exists())
        self.assertTrue((pub.out_dir / "assets" / "prefabs.css").exists())
        index = json.loads((pub.out_dir / "data" / "docs.json").read_text(encoding="utf-8"))
        self.assertTrue(any(d["id"] == "plan" and d["page"] == "/YamlStaticSite/plan/" for d in index))
        manifest = json.loads((pub.out_dir / "build.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["target"], "public")

    def test_forbidden_string_fails_public_build_and_removes_output(self):
        (self.root / "docs" / "leaky.yaml").write_text(
            "kind: generic\ntitle: Leaky\nsummary: contains SUPERSECRETNAME here\ndata: {}\n", encoding="utf-8"
        )
        os.environ["YSS_FORBIDDEN_STRINGS"] = "supersecretname"
        cfg = Config.load(self.root)
        with self.assertRaises(BuildError) as cm:
            build(cfg, "public", run_dynamic=False)
        self.assertIn("forbidden string", str(cm.exception))
        self.assertIn("s*************e", str(cm.exception))  # masked, never printed in full
        self.assertFalse(cfg.out_dir("public").exists())
        report = build(cfg, "private", run_dynamic=False)  # private is not redacted
        self.assertTrue(report.out_dir.exists())

    def test_flag_strings_warn_and_strict_fails(self):
        (self.root / "docs" / "flagged.yaml").write_text(
            "kind: generic\ntitle: Flagged\nsummary: mentions WATCHWORD once\ndata: {}\n", encoding="utf-8"
        )
        os.environ["YSS_FLAG_STRINGS"] = "watchword"
        cfg = Config.load(self.root)
        report = build(cfg, "public", run_dynamic=False)
        self.assertTrue(report.flags)
        with self.assertRaises(BuildError):
            build(cfg, "public", run_dynamic=False, strict=True)

    def test_root_path_is_always_forbidden_in_public(self):
        (self.root / "docs" / "pathy.yaml").write_text(
            f"kind: generic\ntitle: Pathy\nsummary: built at {self.root.as_posix()}\ndata: {{}}\n", encoding="utf-8"
        )
        with self.assertRaises(BuildError):
            build(Config.load(self.root), "public", run_dynamic=False)

    def test_private_doc_binding_in_public_page_gives_helpful_error(self):
        (self.root / "docs" / "hidden.yaml").write_text(
            "kind: generic\ntitle: Hidden\nvisibility: private\ndata: [{a: 1}]\n", encoding="utf-8"
        )
        (self.root / "site" / "pages" / "peek.yaml").write_text(
            "title: Peek\nsections:\n  - type: prefab\n    prefab: table\n    args:\n      rows: {from: hidden.data}\n", encoding="utf-8"
        )
        cfg = Config.load(self.root)
        with self.assertRaises(BuildError) as cm:
            build(cfg, "public", run_dynamic=False)
        self.assertIn("visibility: private", str(cm.exception))
        build(cfg, "private", run_dynamic=False)  # fine privately


class PrefabTests(TempSiteCase):
    def test_every_prefab_example_renders(self):
        loaded = load_all(self.cfg)
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        for name, spec in loaded.prefabs.items():
            for example in spec.get("examples") or []:
                html = renderer.prefab(name, example["args"])
                self.assertTrue(html.strip(), f"{name} example rendered nothing")

    def test_missing_required_param_is_reported(self):
        loaded = load_all(self.cfg)
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        with self.assertRaises(Exception) as cm:
            renderer.prefab("card", {})
        self.assertIn("missing required param 'title'", str(cm.exception))


class ServerTests(TempSiteCase):
    def test_public_server_honours_base_url(self):
        from yss.server import make_server

        build(self.cfg, "public", run_dynamic=False)
        server = make_server(self.cfg, "public", "127.0.0.1", 0, live=False)
        port = server.server_address[1]
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
            conn.request("GET", "/YamlStaticSite/plan/")
            resp = conn.getresponse()
            body = resp.read().decode("utf-8")
            self.assertEqual(resp.status, 200)
            self.assertIn("<h1>Plan</h1>", body)
            self.assertEqual(resp.getheader("Cross-Origin-Embedder-Policy"), "require-corp")
            conn.request("GET", "/")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 302)
            self.assertEqual(resp.getheader("Location"), "/YamlStaticSite/")
            conn.request("GET", "/elsewhere/")
            resp = conn.getresponse()
            resp.read()
            self.assertEqual(resp.status, 404)
        finally:
            server.shutdown()
            server.server_close()


class CliTests(TempSiteCase):
    def test_query_and_ls_and_schema(self):
        from yss.cli import main

        self.assertEqual(main(["--root", str(self.root), "validate"]), 0)
        self.assertEqual(main(["--root", str(self.root), "query", "plan.milestones", "--where", "status=active", "--fields", "id"]), 0)
        self.assertEqual(main(["--root", str(self.root), "ls", "prefabs"]), 0)
        self.assertEqual(main(["--root", str(self.root), "schema", "doc.plan"]), 0)
        self.assertEqual(main(["--root", str(self.root), "schema", "nope"]), 1)

    def test_new_doc_validates(self):
        from yss.cli import main

        self.assertEqual(main(["--root", str(self.root), "new", "doc", "decisions", "more-decisions"]), 0)
        self.assertEqual(main(["--root", str(self.root), "new", "page", "more", "--doc", "plan"]), 0)
        self.assertEqual(main(["--root", str(self.root), "new", "prefab", "shiny"]), 0)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("more-decisions", loaded.docs)
        self.assertIn("shiny", loaded.prefabs)


if __name__ == "__main__":
    unittest.main()

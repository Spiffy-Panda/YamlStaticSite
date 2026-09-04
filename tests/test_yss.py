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
from yss.build import git_commit  # noqa: E402
from yss.evidence import Claim, _check_export, _check_symbol, collect_claims  # noqa: E402
from yss.providers import symbols as symbols_provider  # noqa: E402
from yss.symbols import SymbolError, file_index, index_for, lookup, supported  # noqa: E402


def temp_site() -> Path:
    """Copy the pilot site's sources (not dist, not .yss) into a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-test-"))
    for name in ("site.yaml", "README.md"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, tmp / name)
    for sub in ("docs", "site", "schemas", "examples"):
        if (REPO / sub).is_dir():
            shutil.copytree(REPO / sub, tmp / sub)
    # the pilot docs cite yss/, tests/ and dist/ as evidence; mirror them so strict builds stay clean
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


class SkillTests(unittest.TestCase):
    def test_repo_skill_copies_match_package(self):
        from yss.skillpack import check, list_skills

        self.assertGreaterEqual(len(list_skills()), 5)
        bad = [(n, s) for n, s in check(REPO) if s != "ok"]
        self.assertEqual(bad, [], "run: python -m yss skills --install --force")

    def test_install_into_fresh_repo(self):
        from yss.skillpack import check, install

        tmp = Path(tempfile.mkdtemp(prefix="yss-skills-"))
        try:
            statuses = {s for _, s in install(tmp)}
            self.assertEqual(statuses, {"installed"})
            self.assertTrue(all(s == "ok" for _, s in check(tmp)))
            (tmp / ".claude" / "skills" / "yss" / "SKILL.md").write_text("edited", encoding="utf-8")
            self.assertIn(("yss", "kept"), install(tmp))
            self.assertIn(("yss", "updated"), install(tmp, force=True))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class CliTests(TempSiteCase):
    def test_query_and_ls_and_schema(self):
        from yss.cli import main

        self.assertEqual(main(["--root", str(self.root), "validate"]), 0)
        self.assertEqual(main(["--root", str(self.root), "query", "plan.milestones", "--where", "status=active", "--fields", "id"]), 0)
        self.assertEqual(main(["--root", str(self.root), "ls", "prefabs"]), 0)
        self.assertEqual(main(["--root", str(self.root), "schema", "doc.plan"]), 0)
        self.assertEqual(main(["--root", str(self.root), "schema", "nope"]), 1)
        self.assertEqual(main(["--root", str(self.root), "skills"]), 1)  # temp copy has no skills yet
        self.assertEqual(main(["--root", str(self.root), "skills", "--install"]), 0)
        self.assertEqual(main(["--root", str(self.root), "skills"]), 0)

    def test_new_doc_validates(self):
        from yss.cli import main

        self.assertEqual(main(["--root", str(self.root), "new", "doc", "decisions", "more-decisions"]), 0)
        for kind in ("plan", "design", "codemap", "glossary", "changelog", "generic"):
            self.assertEqual(main(["--root", str(self.root), "new", "doc", kind, f"new-{kind}"]), 0)
        self.assertEqual(main(["--root", str(self.root), "new", "page", "more", "--doc", "plan"]), 0)
        self.assertEqual(main(["--root", str(self.root), "new", "prefab", "shiny"]), 0)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIn("more-decisions", loaded.docs)
        self.assertIn("shiny", loaded.prefabs)


class SymbolResolverTests(unittest.TestCase):
    """The AST index behind code map deep links (adr-024). Parsing only - nothing is imported."""

    def test_top_level_function_and_class(self):
        names = index_for(REPO, "yss/symbols.py")
        start, end, file = names["index_for"]
        self.assertLess(start, end)
        self.assertEqual(file, "yss/symbols.py")
        self.assertIn("SymbolError", names)

    def test_dotted_class_member(self):
        """`Config.evidence_for` is the case `hasattr` can never answer."""
        span = lookup(REPO, "yss/config.py", "Config.evidence_for")
        self.assertIsNotNone(span)
        line = (REPO / "yss/config.py").read_text(encoding="utf-8").splitlines()[span[0] - 1]
        self.assertIn("def evidence_for", line)

    def test_module_level_constant(self):
        span = lookup(REPO, "yss/config.py", "DEFAULTS")
        self.assertIsNotNone(span)
        first = (REPO / "yss/config.py").read_text(encoding="utf-8").splitlines()[span[0] - 1]
        self.assertTrue(first.startswith("DEFAULTS"))

    def test_package_directory_submodule_export(self):
        """A module entry may name a directory; the range belongs to a file inside it."""
        span = lookup(REPO, "yss/providers/", "buildinfo.collect")
        self.assertIsNotNone(span)
        self.assertEqual(span[2], "yss/providers/buildinfo.py")

    def test_non_python_module_is_unsupported(self):
        self.assertFalse(supported("yss/assets/yss.js"))
        self.assertTrue(supported("yss/config.py"))
        self.assertTrue(supported("yss/providers/"))
        self.assertEqual(index_for(REPO, "yss/assets/yss.js"), {})

    def test_missing_name_resolves_to_none(self):
        self.assertIsNone(lookup(REPO, "yss/config.py", "no_such_export"))

    def test_nested_helper_does_not_shadow_a_real_export(self):
        """A function defined inside another function is not an export and must not be indexed."""
        tmp = Path(tempfile.mkdtemp(prefix="yss-sym-"))
        try:
            f = tmp / "m.py"
            f.write_text("def outer():\n    def collect():\n        pass\n    return collect\n", encoding="utf-8")
            names = file_index(f, "m.py")
            self.assertIn("outer", names)
            self.assertNotIn("collect", names)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)

    def test_unparsable_file_reports_a_relative_path(self):
        tmp = Path(tempfile.mkdtemp(prefix="yss-sym-"))
        try:
            f = tmp / "broken.py"
            f.write_text("def (:\n", encoding="utf-8")
            with self.assertRaises(SymbolError) as caught:
                file_index(f, "pkg/broken.py")
            self.assertEqual(caught.exception.rel, "pkg/broken.py")
            self.assertNotIn(str(tmp), str(caught.exception))
        finally:
            shutil.rmtree(tmp, ignore_errors=True)


class SymbolProviderTests(TempSiteCase):
    """The dynamic sources that carry the index, and privately the source text."""

    def test_public_index_has_ranges_and_no_absolute_paths(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public"})
        self.assertTrue(data["modules"])
        self.assertEqual(data["errors"], [])
        self.assertNotIn(str(self.root), json.dumps(data))
        for spans in data["modules"].values():
            for span in spans.values():
                self.assertEqual(len(span), 3)
                self.assertLessEqual(span[0], span[1])
                self.assertTrue((self.root / span[2]).is_file(), f"{span[2]} should be a real file")

    def test_every_pilot_export_resolves(self):
        data = symbols_provider.collect(self.cfg, {"_target": "public"})
        self.assertEqual(data["unresolved"], [], "a code map export no longer exists where the doc says")

    def test_private_module_is_absent_from_the_public_index(self):
        codemap = self.root / "docs" / "codemap.yaml"
        text = codemap.read_text(encoding="utf-8")
        marked = text.replace("  - id: config\n", "  - id: config\n    visibility: private\n", 1)
        self.assertNotEqual(text, marked, "expected a `config` module in the pilot code map")
        codemap.write_text(marked, encoding="utf-8")
        cfg = Config.load(self.root)
        public = symbols_provider.collect(cfg, {"_target": "public"})
        private = symbols_provider.collect(cfg, {"_target": "private"})
        self.assertNotIn("yss/config.py", public["modules"])
        self.assertIn("yss/config.py", private["modules"])

    def test_source_text_is_private_only(self):
        data = symbols_provider.collect_source(self.cfg, {"_target": "private"})
        self.assertTrue(data["text"])
        sample = next(iter(next(iter(data["text"].values())).values()))
        self.assertEqual(len(sample["lines"]), sample["end"] - sample["start"] + 1)
        with self.assertRaises(RuntimeError):
            symbols_provider.collect_source(self.cfg, {"_target": "public"})


class ExportEvidenceTests(TempSiteCase):
    """`x-evidence: export` proves each code map export still sits where the site links to it."""

    def test_export_claim_resolves_against_the_enclosing_path(self):
        claim = Claim("codemap", None, "f", "export", "yss/config.py::Config.evidence_for")
        _check_export(self.cfg, claim)
        self.assertEqual(claim.status, "ok")
        self.assertIn("yss/config.py:", claim.detail)

    def test_vanished_export_is_stale(self):
        claim = Claim("codemap", None, "f", "export", "yss/config.py::gone_away")
        _check_export(self.cfg, claim)
        self.assertEqual(claim.status, "stale")

    def test_non_python_export_is_skipped_not_stale(self):
        claim = Claim("codemap", None, "f", "export", "yss/assets/yss.js::whatever")
        _check_export(self.cfg, claim)
        self.assertEqual(claim.status, "skipped")

    def test_symbol_claim_resolves_a_dotted_member_without_importing(self):
        claim = Claim("x", None, "f", "symbol", "yss.config:Config.evidence_for")
        _check_symbol(self.cfg, claim)
        self.assertEqual(claim.status, "ok")

    def test_entrypoint_names_do_not_become_export_claims(self):
        """The annotation is per field name, so `entrypoints[].name` must not be claimed."""
        loaded = load_all(self.cfg)
        exports = [c for c in collect_claims(loaded.docs, loaded.registry) if c.kind == "export"]
        self.assertTrue(exports)
        self.assertEqual([c for c in exports if c.field.startswith("entrypoints")], [])
        self.assertTrue(all("::" in c.target for c in exports))


class BuildCommitTests(TempSiteCase):
    """Deep links are only true of one commit, so the build has to know which (adr-024)."""

    def test_build_info_shape(self):
        info = git_commit(self.root)
        self.assertEqual(set(info), {"commit", "commit_short", "dirty"})
        self.assertIsInstance(info["dirty"], bool)

    def test_github_sha_wins_over_the_working_tree(self):
        os.environ["GITHUB_SHA"] = "a" * 40
        try:
            info = git_commit(self.root)
        finally:
            os.environ.pop("GITHUB_SHA", None)
        self.assertEqual(info["commit"], "a" * 40)
        self.assertEqual(info["commit_short"], "aaaaaaa")
        self.assertFalse(info["dirty"])

    def test_a_dirty_build_does_not_publish_line_anchors(self):
        """An unpinned build still links to the file; it just must not claim a line range."""
        html = Renderer(
            self.cfg, "public", {}, [], load_all(self.cfg).prefabs, [], {"commit": None, "dirty": True}, []
        ).prefab(
            "module-list",
            modules=[{"id": "cfg", "path": "yss/config.py", "purpose": "x", "exports": [{"name": "deep_merge"}]}],
            repo_url="https://example.invalid/r",
            commit=None,
            dirty=True,
        )
        self.assertIn("/blob/main/yss/config.py", html)
        self.assertNotIn("#L", html)
        self.assertIn('data-pinned="0"', html)

if __name__ == "__main__":
    unittest.main()

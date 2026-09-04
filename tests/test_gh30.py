"""Regression tests for gh-30: reserve `_src` and `_doc` on bound items.

Explicitly the cheap half of a larger feature, and explicitly the half that must not be deferred.
`prefab()` copies every arg key into the template namespace and type-checks only *declared* params,
which is why `card` can already read `_evidence` off an item that never declared it. So a prefab
written between now and per-item attribution either lands compatible or does not, depending on a
convention nobody has written down - and prefabs are being written now, during the Musings port.

The stamping is onto *copies*, never in place. The build dumps `docs` to `data/docs/<id>.json`
after rendering, so an in-place stamp would put `_src` into every JSON export - which is the trap
in "mirror how `_type` is stamped": `doc_items` copies, but `resolve_from` returns the doc's own
live list.

Deliberately out of scope, and pinned here so it stays deliberate: `fields:` drops `_src`, because
it rebuilds each item from the named keys only. That is honest - a `fields:`-narrowed table falls
back to region-level attribution because the page author chose to discard everything else.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.binding import resolve_binding  # noqa: E402
from yss.build import build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class StampCase(TempSiteCase):
    def setUp(self):
        super().setUp()
        self.loaded = load_all(self.cfg)
        self.assertEqual(self.loaded.errors, [])
        self.ctx = {"docs": self.loaded.docs, "pages": self.loaded.pages, "site": self.cfg.site}

    def bind(self, **spec):
        return resolve_binding(spec, self.ctx, render=lambda t, c: t)


class StampingTests(StampCase):
    def test_a_plain_list_binding_carries_the_source(self):
        items = self.bind(**{"from": "plan.milestones"})
        self.assertTrue(items)
        for item in items:
            self.assertEqual(item["_doc"], "plan")
            self.assertTrue(item["_src"].endswith("plan.yaml"), item["_src"])

    def test_a_filtered_binding_carries_it_too(self):
        items = self.bind(**{"from": "plan.milestones", "where": {"status": "planned"}})
        self.assertTrue(items)
        self.assertTrue(all(i["_src"] for i in items))

    def test_it_survives_map(self):
        """`map_items` starts from `dict(item)`, so unmapped keys ride along."""
        items = self.bind(**{"from": "plan.milestones", "map": {"label": "{{ title }}"}})
        self.assertTrue(all(i["_doc"] == "plan" for i in items), items[0] if items else None)

    def test_it_survives_group_by_into_the_buckets(self):
        """`group_by` yields bucket dicts rather than items, so the stamp has to be inside."""
        groups = self.bind(**{"from": "design.$items", "group_by": "group"})
        inner = [item for g in groups for item in g["items"]]
        self.assertTrue(inner)
        self.assertTrue(all(i["_doc"] == "design" for i in inner))

    def test_the_doc_local_items_root_stamps_alongside_type(self):
        items = self.bind(**{"from": "design.$items"})
        self.assertTrue(items)
        for item in items:
            self.assertIn("_type", item)
            self.assertEqual(item["_doc"], "design")

    def test_a_collection_doc_stamps_its_global_id(self):
        items = resolve_binding({"from": "plan.milestones"}, {**self.ctx, "collection": "demo-musing"})
        self.assertTrue(items)
        self.assertTrue(all(i["_doc"] == "demo-musing/plan" for i in items))


class AbsenceTests(StampCase):
    def test_virtual_roots_are_not_stamped(self):
        """`source_doc` returns None for a `$root`; there is no one file to name."""
        for expr in ("$docs", "$pages", "$prefabs"):
            with self.subTest(root=expr):
                items = resolve_binding({"from": expr}, {**self.ctx, "prefabs": self.loaded.prefabs})
                self.assertTrue(items)
                self.assertFalse(any("_src" in i for i in items if isinstance(i, dict)))

    def test_fields_drops_the_stamp_on_purpose(self):
        """Out of scope by decision: the author chose to discard everything but the columns."""
        items = self.bind(**{"from": "plan.milestones", "fields": ["id", "title"]})
        self.assertTrue(items)
        self.assertFalse(any("_src" in i for i in items))

    def test_a_scalar_binding_is_untouched(self):
        self.assertIsInstance(self.bind(**{"from": "design.overview"}), str)


class NoMutationTests(TempSiteCase):
    def test_the_loaded_docs_are_not_stamped_in_place(self):
        loaded = load_all(self.cfg)
        ctx = {"docs": loaded.docs, "pages": loaded.pages, "site": self.cfg.site}
        resolve_binding({"from": "plan.milestones"}, ctx)
        self.assertFalse(any("_src" in m for m in loaded.docs["plan"]["milestones"]))

    def test_the_json_export_carries_no_stamp(self):
        """The build writes docs to data/docs/<id>.json *after* rendering."""
        build(self.cfg, "private", run_dynamic=False)
        data = json.loads((self.root / "dist" / "private" / "data" / "docs" / "plan.json").read_text(encoding="utf-8"))
        self.assertTrue(data["milestones"])
        for milestone in data["milestones"]:
            self.assertNotIn("_src", milestone)
            self.assertNotIn("_doc", milestone)


class MetadataStaysOutOfSightTests(TempSiteCase):
    """Stamping is only free if nothing renders the stamp by accident.

    `table` derives its columns from `rows[0].keys()` when the page gives none, so the moment
    `_src` and `_doc` arrived they became two visible columns on every such table - including the
    demo collection's generated inventory. Derived columns skip `_`-prefixed keys, which is the
    same rule that already made `_type` and `_evidence` invisible.
    """

    def test_derived_columns_skip_item_metadata(self):
        build(self.cfg, "private", run_dynamic=False)
        html = (self.root / "dist" / "private" / "demo-musing" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<th>name</th>", html, "the real columns should still be there")
        self.assertNotIn("<th>_src</th>", html)
        self.assertNotIn("<th>_doc</th>", html)
        self.assertNotIn("<th>_type</th>", html)

    def test_an_explicit_column_list_is_honoured_as_written(self):
        """An author who names `_src` gets it; only the derived list filters."""
        (self.root / "site" / "pages" / "cols.yaml").write_text(
            "id: cols\nroute: /cols/\ntitle: Cols\ndocs: [plan]\n"
            "sections:\n"
            "  - id: t\n    type: prefab\n    prefab: table\n"
            "    args:\n"
            "      rows: {from: plan.milestones}\n"
            "      columns: [id, _src]\n",
            encoding="utf-8")
        build(Config.load(self.root), "private", run_dynamic=False)
        html = (self.root / "dist" / "private" / "cols" / "index.html").read_text(encoding="utf-8")
        self.assertIn("<th>_src</th>", html)


class ConventionIsDocumentedTests(unittest.TestCase):
    def test_the_prefab_skill_tells_authors_what_to_do_with_it(self):
        """The whole point of reserving the name now is that it is written down."""
        for path in (REPO / "yss" / "skills" / "yss-prefab" / "SKILL.md",
                     REPO / ".claude" / "skills" / "yss-prefab" / "SKILL.md"):
            with self.subTest(path=path.name):
                text = path.read_text(encoding="utf-8")
                self.assertIn("_src", text)
                self.assertIn("data-src", text)


if __name__ == "__main__":
    unittest.main()

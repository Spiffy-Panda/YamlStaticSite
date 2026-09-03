"""Regression tests for GH-8: a subject-grouping tier with authored headings and blurbs.

The design landed on shape (a) from the issue: an optional doc-level `groups: [{id, title, blurb}]`
on the *envelope* (so every kind gets it for free), plus an optional `group: <id>` on `item_base`
carrying `x-ref: item`, so a typo fails validation through the existing reference checker. The
binding does the joining: when `group_by` is used, resolve_binding looks up the source doc's
`groups` and merges each authored group onto its bucket, so a prefab reads `g.title` / `g.blurb`
instead of a bare key, and the doc's declaration order becomes display order.

Nothing about this is opt-out: a doc with no `groups:` and items with no `group:` produces exactly
the `[{key, items}]` shape group_by always produced.

Unlike GH-7's `status`, `group` is safe to put on item_base: it carries no vocabulary, so there is
no `allOf` intersection for a kind-specific declaration to collide with. The last test pins that.

Run with: python -m unittest tests.test_gh08 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss.binding import resolve_binding  # noqa: E402
from yss.build import load_all  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

GROUPED_DOC = """kind: design
id: gh8
title: GH8 grouped design
groups:
  - id: the-room
    title: The room and the hands
    blurb: |
      The pod, the chair you do not leave, and the single pair of hands every manual job
      has to share.
  - id: the-loop
    title: The loop
    blurb: One turn of the thing, start to finish.
principles:
  - {id: pr1, title: Hands are scarce, group: the-room}
components:
  - {id: c1, name: Pod, responsibility: Holds the crew., group: the-room}
  - {id: c2, name: Turn, responsibility: Advances the clock., group: the-loop}
  - {id: c3, name: Stray, responsibility: Belongs to no subject.}
constraints:
  - {id: k1, text: One pair of hands., group: the-room}
"""

UNGROUPED_DOC = """kind: design
id: gh8flat
title: GH8 flat design
components:
  - {id: c1, name: Pod, responsibility: Holds the crew., group: nope}
"""


class GroupSchemaTests(TempSiteCase):
    def test_doc_with_groups_validates(self):
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        doc = loaded.docs["gh8"]
        self.assertEqual([g["id"] for g in doc["groups"]], ["the-room", "the-loop"])
        self.assertEqual(doc["groups"][0]["title"], "The room and the hands")
        self.assertIn("single pair of hands", doc["groups"][0]["blurb"])
        self.assertEqual(doc["components"][0]["group"], "the-room")

    def test_unknown_group_fails_validation(self):
        self.write_doc("gh8flat.yaml", UNGROUPED_DOC)
        loaded = load_all(self.cfg)
        self.assertTrue(
            any(
                "docs/gh8flat.yaml" in e and "components/0/group" in e and "no item 'nope'" in e
                for e in loaded.errors
            ),
            loaded.errors,
        )

    def test_group_id_is_an_indexed_item_so_inline_refs_resolve(self):
        self.write_doc("gh8.yaml", GROUPED_DOC)
        self.write_doc("gh8ref.yaml", "kind: generic\ntitle: R\nsummary: see [[gh8#the-room]]\ndata: {}\n")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.write_doc("gh8ref.yaml", "kind: generic\ntitle: R\nsummary: see [[gh8#the-attic]]\ndata: {}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(
            any("inline reference [[gh8#the-attic]]" in e for e in loaded.errors), loaded.errors
        )

    def test_group_carries_no_vocabulary_so_it_cannot_intersect(self):
        """The GH-7 trap in reverse: item_base.group is a plain slug, so kinds with their own
        status vocabularies keep validating and a group id is never checked against an enum."""
        self.write_doc(
            "gh8plan.yaml",
            "kind: plan\nid: gh8plan\ntitle: GH8 plan\n"
            "groups:\n  - {id: shipping, title: Shipping}\n"
            "milestones:\n  - {id: m1, title: M1, status: planned, group: shipping}\n"
            "risks:\n  - {id: r1, title: R1, status: open, group: shipping}\n",
            )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["gh8plan"]["milestones"][0]["status"], "planned")


class GroupBindingTests(TempSiteCase):
    def ctx(self, loaded) -> dict:
        return {"docs": loaded.docs, "pages": loaded.pages, "site": {}}

    def test_group_by_yields_authored_title_and_blurb(self):
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        got = resolve_binding({"from": "gh8.components", "group_by": "group"}, self.ctx(loaded))
        self.assertEqual([g["key"] for g in got], ["the-room", "the-loop", None])
        self.assertEqual(got[0]["title"], "The room and the hands")
        self.assertIn("single pair of hands", got[0]["blurb"])
        self.assertEqual([i["id"] for i in got[0]["items"]], ["c1"])
        # A key that names no declared group stays a bare bucket - no invented heading.
        self.assertIsNone(got[2]["key"])
        self.assertNotIn("title", got[2])
        self.assertEqual([i["id"] for i in got[2]["items"]], ["c3"])

    def test_declaration_order_wins_over_appearance_order(self):
        """`the-loop` is declared second but its only member appears before the-room's in a
        reversed list; the doc's declared order still decides display order."""
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        got = resolve_binding(
            {"from": "gh8.components", "sort": "-id", "group_by": "group"}, self.ctx(loaded)
        )
        self.assertEqual([g["key"] for g in got], ["the-room", "the-loop", None])

    def test_one_blurb_reaches_every_array_that_groups_by_it(self):
        """The point of the feature: written once, available to principles, components and
        constraints alike - and therefore to any page that binds any of them."""
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        ctx = self.ctx(loaded)
        for path in ("gh8.principles", "gh8.components", "gh8.constraints"):
            got = resolve_binding({"from": path, "group_by": "group"}, ctx)
            room = next(g for g in got if g["key"] == "the-room")
            self.assertEqual(room["title"], "The room and the hands")
            self.assertIn("single pair of hands", room["blurb"])

    def test_doc_without_groups_behaves_exactly_as_before(self):
        """No `groups:` in the source doc -> the historic [{key, items}] shape, appearance order,
        and no extra keys on the bucket."""
        self.write_doc(
            "gh8none.yaml",
            "kind: plan\nid: gh8none\ntitle: GH8 none\n"
            "milestones:\n"
            "  - {id: m1, title: M1, status: active}\n"
            "  - {id: m2, title: M2, status: planned}\n"
            "  - {id: m3, title: M3, status: active}\n",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        got = resolve_binding(
            {"from": "gh8none.milestones", "group_by": "status"}, self.ctx(loaded)
        )
        self.assertEqual([g["key"] for g in got], ["active", "planned"])
        self.assertEqual([sorted(g) for g in got], [["items", "key"], ["items", "key"]])
        self.assertEqual([i["id"] for i in got[0]["items"]], ["m1", "m3"])

    def test_map_runs_before_grouping_so_item_title_and_group_title_coexist(self):
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        got = resolve_binding(
            {"from": "gh8.components", "map": {"title": "name"}, "group_by": "group"},
            self.ctx(loaded),
        )
        self.assertEqual(got[0]["title"], "The room and the hands")
        self.assertEqual(got[0]["items"][0]["title"], "Pod")


class GroupPrefabTests(TempSiteCase):
    def test_group_sections_renders_heading_blurb_and_items(self):
        self.write_doc("gh8.yaml", GROUPED_DOC)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        groups = resolve_binding(
            {"from": "gh8.components", "map": {"title": "name", "body": "responsibility"},
             "group_by": "group"},
            {"docs": loaded.docs, "pages": loaded.pages, "site": {}},
        )
        html = str(renderer.prefab("group-sections", groups=groups, item_args={"columns": 2}))
        self.assertIn("The room and the hands", html)
        self.assertIn("single pair of hands", html)
        self.assertIn("The loop", html)
        self.assertIn("Pod", html)
        # The undeclared bucket falls back to a label rather than printing "None".
        self.assertIn("Ungrouped", html)
        self.assertNotIn(">None<", html)

    def test_pilot_design_page_shows_the_authored_groups(self):
        """End to end on this repository's own design doc and page."""
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        design = loaded.docs["design"]
        ids = [g["id"] for g in design["groups"]]
        self.assertEqual(ids, ["g-sources", "g-trust", "g-presentation", "g-pipeline"])
        # every principle, component and constraint names one of them
        for field in ("principles", "components", "constraints"):
            for item in design[field]:
                self.assertIn(item.get("group"), ids, f"{field}/{item['id']}")
        page = next(p for p in loaded.pages if p["id"] == "design")
        section = next(s for s in page["sections"] if s.get("id") == "components")
        self.assertEqual(section["prefab"], "group-sections")
        self.assertEqual(section["args"]["groups"]["group_by"], "group")


if __name__ == "__main__":
    unittest.main()

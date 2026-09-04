"""Regression tests for gh-12: `group-sections` emitted duplicate HTML ids when a group spanned
several type arrays, and no binding could span them in the first place.

Three separate holes, one issue:

* A binding reads one array (`design.components`), so a subject group that also owns principles and
  constraints could only be rendered by three sections - three `group-sections` blocks, each
  emitting `id="group-<slugged title>"` for the *same* group. `design.$items` closes it: one
  virtual root over every type array in a doc, each item tagged `_type`.
* `group-sections` derived its anchor from the group's *label*, so the id was neither stable nor
  unique. A declared group now anchors under its own group id verbatim (adr-023's rule for table
  rows), and `id_prefix` exists for a page that deliberately renders one group twice.
* Nothing noticed. `Renderer.dead_refs()` tests set membership, so a duplicated anchor looks
  exactly like a present one. The build now counts ids per page.

The issue also blamed `map:` for collapsing `group_by` buckets to None. It does not - `map_items`
starts from `dict(item)`, so `group` survives. `fields:` is the culprit, and
`test_fields_before_group_by` pins both halves of that.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import contextlib
import io
import json
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.binding import BindError, doc_items, resolve_binding  # noqa: E402
from yss.build import BuildError, build, load_all  # noqa: E402
from yss.cli import main as cli_main  # noqa: E402
from yss.render import ANCHOR_RE, Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

# Two type arrays sharing one set of authored groups, next to the envelope lists (`owners`,
# `related`) and a string list (`notes`) that `$items` has to step over.
FLAT_DOC = """\
kind: generic
id: gh12
title: GH12 fixture
summary: Two type arrays grouped by the same subjects, so a binding has to span both.
status: active
updated: '2026-09-03'
owners: [agent]
related: [plan]
groups:
  - id: g-left
    title: The left subject
    blurb: Everything about the left half.
  - id: g-right
    title: The right subject
x-rules:
  - {id: r-one, group: g-left, title: First rule}
  - {id: r-two, group: g-right, title: Second rule}
x-parts:
  - {id: p-one, group: g-left, title: First part}
  - {id: p-two, group: g-left, title: Second part}
  - {id: p-loose, title: A part in no group}
x-notes: [not an item list]
data: {}
"""

PAGE_HEAD = """\
id: gh12
route: /gh12/
title: GH12 fixture
summary: Renders the fixture doc's subject groups.
nav: {hidden: true}
docs: [gh12]
sections:
"""

SECTION = """\
  - id: {sid}
    type: prefab
    prefab: group-sections
    args:
      groups:
        from: gh12.$items
        where: {{_type: [{types}]}}
        group_by: group
{extra}
"""


def section(sid: str, types: str = "x-rules, x-parts", id_prefix: str | None = None) -> str:
    """One `group-sections` section over the fixture's items.

    `types` narrows which type arrays it draws from: two sections that select *different* arrays
    still land in the same authored group, which is the collision this issue is about - the group
    heading is what repeats, not the items under it.
    """
    extra = f"      id_prefix: {id_prefix}\n" if id_prefix else ""
    return SECTION.format(sid=sid, types=types, extra=extra)


class Gh12Case(TempSiteCase):
    def write_fixture(self, page: str | None = None, doc: str = FLAT_DOC) -> None:
        self.write_doc("gh12.yaml", doc)
        if page is not None:
            (self.root / "site" / "pages" / "gh12.yaml").write_text(page, encoding="utf-8")

    def loaded(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        return loaded

    def ctx(self) -> dict:
        loaded = self.loaded()
        return {
            "docs": loaded.docs,
            "pages": loaded.pages,
            "site": self.cfg.site,
            "prefabs": loaded.prefabs,
            "all_doc_ids": list(loaded.docs),
        }

    def report(self, strict: bool = False):
        return build(self.cfg, "private", out_dir=self.root / "out", strict=strict, run_dynamic=False)

    def html(self, route: str = "gh12") -> str:
        return (self.root / "out" / route / "index.html").read_text(encoding="utf-8")


class ItemsRootTests(Gh12Case):
    def test_items_flattens_type_arrays_and_tags_each_item(self):
        self.write_fixture()
        items = resolve_binding({"from": "gh12.$items"}, self.ctx())
        self.assertEqual(
            [(it["id"], it["_type"]) for it in items],
            [
                ("r-one", "x-rules"),
                ("r-two", "x-rules"),
                ("p-one", "x-parts"),
                ("p-two", "x-parts"),
                ("p-loose", "x-parts"),
            ],
        )

    def test_items_skips_envelope_lists_and_metadata(self):
        self.write_fixture()
        items = resolve_binding({"from": "gh12.$items"}, self.ctx())
        types = {it["_type"] for it in items}
        for skipped in ("groups", "related", "owners", "links", "evidence", "tags"):
            self.assertNotIn(skipped, types)
        self.assertFalse([t for t in types if t.startswith("_")], types)

    def test_items_ignores_lists_that_are_not_lists_of_mappings(self):
        """`owners: [agent]` and `related: [plan]` are lists of strings, not items."""
        doc = {"title": "T", "owners": ["agent"], "notes": ["a", "b"], "things": [{"id": "x"}]}
        self.assertEqual(doc_items(doc), [{"id": "x", "_type": "things"}])

    def test_items_reads_the_pilot_design_doc(self):
        items = resolve_binding({"from": "design.$items"}, self.ctx())
        types = {it["_type"] for it in items}
        self.assertLessEqual({"principles", "components", "constraints"}, types)
        self.assertNotIn("groups", types)

    def test_where_filters_on_type(self):
        self.write_fixture()
        items = resolve_binding(
            {"from": "gh12.$items", "where": {"_type": ["x-parts"]}}, self.ctx()
        )
        self.assertEqual([it["id"] for it in items], ["p-one", "p-two", "p-loose"])

    def test_type_is_mappable(self):
        self.write_fixture()
        items = resolve_binding({"from": "gh12.$items", "map": {"badge": "_type"}}, self.ctx())
        self.assertEqual(items[0]["badge"], "x-rules")

    def test_group_by_over_items_yields_each_authored_group_once(self):
        self.write_fixture()
        groups = resolve_binding(
            {"from": "gh12.$items", "where": {"_type": ["x-rules", "x-parts"]}, "group_by": "group"},
            self.ctx(),
        )
        self.assertEqual([g["key"] for g in groups], ["g-left", "g-right", None])
        left = next(g for g in groups if g["key"] == "g-left")
        self.assertEqual(left["title"], "The left subject")          # the authored group came along
        self.assertEqual([it["id"] for it in left["items"]], ["r-one", "p-one", "p-two"])
        self.assertEqual({it["_type"] for it in left["items"]}, {"x-rules", "x-parts"})  # spans arrays

    def test_only_the_exact_suffix_is_a_root(self):
        self.write_fixture()
        with self.assertRaises(BindError) as ctx:
            resolve_binding({"from": "gh12.$items.0"}, self.ctx())
        self.assertIn("$items", str(ctx.exception))

    def test_items_from_a_collection_page(self):
        """Inside a collection, `plan.$items` means that collection's own plan doc."""
        ctx = dict(self.ctx(), collection="demo-musing")
        local = resolve_binding({"from": "plan.$items"}, ctx)
        root = resolve_binding({"from": "/plan.$items"}, ctx)
        self.assertTrue(local)
        self.assertIn("milestones", {it["_type"] for it in local})
        self.assertNotEqual(
            [it.get("id") for it in local], [it.get("id") for it in root]
        )

    def test_cmd_query_resolves_items(self):
        """`yss query` builds the same spec a page does, so `$items` has to work there too."""
        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = cli_main(
                ["--root", str(self.root), "query", "design.$items",
                 "--where", "_type=components", "--fields", "id,_type"]
            )
        self.assertEqual(code, 0)
        rows = json.loads(buf.getvalue())
        self.assertTrue(rows)
        self.assertEqual({r["_type"] for r in rows}, {"components"})


class GroupAnchorTests(Gh12Case):
    def renderer(self) -> Renderer:
        loaded = self.loaded()
        return Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)

    def render(self, **args) -> str:
        return str(self.renderer().prefab("group-sections", args))

    def test_a_declared_group_anchors_under_its_own_id(self):
        html = self.render(groups=[{"key": "g-left", "title": "The left subject", "items": [{"title": "A"}]}])
        self.assertIn('id="g-left"', html)
        self.assertNotIn('id="group-the-left-subject"', html)

    def test_an_undeclared_bucket_keeps_the_slugged_fallback(self):
        html = self.render(groups=[{"key": "loose ends", "items": [{"title": "A"}]}])
        self.assertIn('id="group-loose-ends"', html)

    def test_a_bucket_with_no_key_at_all_still_anchors(self):
        html = self.render(groups=[{"key": None, "items": [{"title": "A"}]}])
        self.assertIn('id="group-ungrouped"', html)

    def test_id_prefix_disambiguates(self):
        html = self.render(
            groups=[{"key": "g-left", "title": "The left subject", "items": [{"title": "A"}]}],
            id_prefix="alt-",
        )
        self.assertIn('id="alt-g-left"', html)
        self.assertNotIn('id="g-left"', html)

    def test_the_pilot_design_page_anchors_the_authored_group_ids(self):
        self.report()
        html = self.html("design")
        for gid in ("g-sources", "g-trust", "g-presentation", "g-pipeline"):
            self.assertIn(f'id="{gid}"', html)

    def test_the_pilot_design_page_shows_all_three_types_in_one_group(self):
        """The reorganisation this issue asked for: one subject section, three kinds of item in it,
        each still labelled so a principle is not mistaken for a constraint."""
        self.report()
        html = self.html("design")
        block = html.split('id="g-presentation"', 1)[1].split("</section>", 1)[0]
        self.assertIn('id="p-static-first"', block)      # a principle
        self.assertIn('id="c-render"', block)            # a component
        self.assertIn('id="k-gh-pages"', block)          # a constraint
        for word in ("principle", "component", "constraint"):
            self.assertIn(f'card-kicker">{word}<', block)


class DuplicateAnchorTests(Gh12Case):
    def dupes(self, report) -> list[str]:
        return [w for w in report.warnings if w.startswith("duplicate anchor id")]

    TWO_SECTIONS = PAGE_HEAD + section("rules", "x-rules") + section("parts", "x-parts")
    PREFIXED = PAGE_HEAD + section("rules", "x-rules") + section("parts", "x-parts", "alt-")

    def test_one_group_in_two_sections_is_reported(self):
        self.write_fixture(self.TWO_SECTIONS)
        dupes = self.dupes(self.report())
        self.assertEqual(dupes, ["duplicate anchor id 'g-left' on page /gh12/ (2 times)"])

    def test_id_prefix_clears_the_warning(self):
        self.write_fixture(self.PREFIXED)
        self.assertEqual(self.dupes(self.report()), [])
        html = self.html()
        self.assertIn('id="g-left"', html)
        self.assertIn('id="alt-g-left"', html)

    def test_strict_build_fails_on_a_duplicate_anchor(self):
        self.write_fixture(self.TWO_SECTIONS)
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", out_dir=self.root / "out", strict=True, run_dynamic=False)
        self.assertIn("duplicate anchor id", str(ctx.exception))

    def test_both_pilot_targets_have_no_duplicate_anchor_ids(self):
        """The repository itself must stay clean - this is what the check is for."""
        for target in ("private", "public"):
            report = build(self.cfg, target, out_dir=self.root / f"out-{target}", run_dynamic=False)
            self.assertEqual(self.dupes(report), [], target)

    def test_every_rendered_pilot_page_really_has_unique_ids(self):
        """Belt and braces: re-derive the counts from the files on disk, not from the report."""
        self.report()
        for path in (self.root / "out").rglob("index.html"):
            ids = [a for a in ANCHOR_RE.findall(path.read_text(encoding="utf-8")) if a]
            self.assertEqual(len(ids), len(set(ids)), path.name)


FIELDS_PAGE = PAGE_HEAD + """\
  - id: dropped
    type: prefab
    prefab: group-sections
    args:
      groups:
        from: gh12.$items
        where: {_type: [x-rules]}
        fields: [id, title]
        group_by: group
"""

MAP_PAGE = PAGE_HEAD + """\
  - id: kept
    type: prefab
    prefab: group-sections
    args:
      groups:
        from: gh12.$items
        where: {_type: [x-rules]}
        map: {badge: _type}
        group_by: group
"""


class FieldsBeforeGroupByTests(Gh12Case):
    def warnings(self, report) -> list[str]:
        return [w for w in report.warnings if "group_by" in w]

    def test_fields_drops_the_group_by_field_and_the_build_says_so(self):
        self.write_fixture(FIELDS_PAGE)
        warnings = self.warnings(self.report())
        self.assertEqual(len(warnings), 1, warnings)
        message = warnings[0]
        self.assertIn("page 'gh12'", message)            # which page
        self.assertIn("section 'dropped'", message)      # which section
        self.assertIn("group_by 'group'", message)       # which field
        self.assertIn("gh12.$items", message)            # which source array
        self.assertIn("`fields:", message)               # and what dropped it

    def test_map_does_not_drop_it(self):
        """The issue blamed `map:`; `map_items` starts from `dict(item)`, so `group` survives."""
        self.write_fixture(MAP_PAGE)
        self.assertEqual(self.warnings(self.report()), [])
        groups = resolve_binding(
            {"from": "gh12.$items", "where": {"_type": ["x-rules"]}, "map": {"badge": "_type"}, "group_by": "group"},
            self.ctx(),
        )
        self.assertEqual([g["key"] for g in groups], ["g-left", "g-right"])

    def test_a_doc_with_genuinely_ungrouped_items_still_warns_only_once(self):
        self.write_fixture()
        seen: list[str] = []
        resolve_binding(
            {"from": "gh12.$items", "where": {"_type": ["x-parts"]}, "fields": ["id"], "group_by": "group"},
            self.ctx(),
            None,
            seen.append,
        )
        self.assertEqual(len(seen), 1, seen)

    def test_no_warning_when_the_grouping_works(self):
        self.write_fixture()
        seen: list[str] = []
        resolve_binding(
            {"from": "gh12.$items", "group_by": "group"}, self.ctx(), None, seen.append
        )
        self.assertEqual(seen, [])

    def test_no_warning_for_an_empty_selection(self):
        self.write_fixture()
        seen: list[str] = []
        resolve_binding(
            {"from": "gh12.$items", "where": {"_type": ["nope"]}, "group_by": "group"},
            self.ctx(),
            None,
            seen.append,
        )
        self.assertEqual(seen, [])


if __name__ == "__main__":
    unittest.main()

"""Regression tests for gh-29: a rendered region did not say which doc query produced it.

The page header prints `Data: plan.json · later.json`, but that is per-page and names the whole doc
set, not the query behind any one region. A reader looking at four cards cannot tell why those four.

Most of this file tests `yss.attribution`, which is a pure function from section YAML to a sentence
and needs no rendering at all. That is the point of computing attribution in `render_section` from
the section spec rather than inside `Renderer._bind`: `_bind` is reached only from the markdown and
prefab handlers, so `dynamic`, `include`, `embed` and `html` - four of the six section types -
would have contributed nothing.

The presentation half is deliberately not a hover pill. A fixed pill has no `mouseover` on touch,
`focusin` needs focusable elements and `<section>` is not one, a `pointer-events: none` pill is
invisible to assistive tech unless it is a live region (and a live region updating on mouse
movement is an anti-pattern), and `yss.css` has no `@media print` for it to be excluded from.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.attribution import VIRTUAL_ROOTS, Attribution, attribute, binding_specs, describe  # noqa: E402

DOCS = {
    "plan": {"id": "plan", "_source": "docs/plan.yaml", "milestones": []},
    "design": {
        "id": "design",
        "_source": "docs/design.yaml",
        "overview": "A paragraph, not a list.",
        "components": [{"id": "c1"}],
        "principles": [{"id": "p1"}],
    },
    "c/plan": {"id": "c/plan", "_source": "c/docs/plan.yaml"},
    "hooked": {"id": "hooked", "_source": "examples/demo-musing/hooks.py:load_docs()[0]"},
}


class BindingSpecTests(unittest.TestCase):
    def test_a_markdown_section_binding_is_found(self):
        specs = binding_specs({"type": "markdown", "from": "design.overview"})
        self.assertEqual(specs, [(None, {"from": "design.overview"})])

    def test_prefab_args_are_found_by_name(self):
        specs = binding_specs({
            "type": "prefab",
            "args": {"items": {"from": "plan.milestones"}, "columns": 2},
        })
        self.assertEqual(specs, [("items", {"from": "plan.milestones"})])

    def test_a_section_with_no_binding_has_none(self):
        self.assertEqual(binding_specs({"type": "html", "html": "<p>hi</p>"}), [])

    def test_literal_args_are_not_mistaken_for_bindings(self):
        self.assertEqual(binding_specs({"args": {"title": "Plan", "level": 3}}), [])


class DescribeTests(unittest.TestCase):
    def phrase(self, spec, **kw):
        return describe(spec, DOCS, **kw)

    def test_a_list_binding_names_its_field_and_its_file(self):
        self.assertEqual(self.phrase({"from": "plan.milestones"}), "milestones in docs/plan.yaml")

    def test_a_where_clause_is_spelled_out(self):
        text = self.phrase({"from": "plan.milestones", "where": {"status": "active"}})
        self.assertEqual(text, "milestones in docs/plan.yaml, where status = active")

    def test_a_list_valued_where_reads_as_a_set(self):
        text = self.phrase({"from": "design.$items", "where": {"_type": ["principles", "components"]}})
        self.assertIn("_type is one of principles, components", text)

    def test_an_operator_where_keeps_its_operator(self):
        text = self.phrase({"from": "plan.milestones", "where": {"priority": {"lte": 2}}})
        self.assertIn("priority lte 2", text)

    def test_every_list_op_has_a_phrase(self):
        text = self.phrase({
            "from": "plan.milestones",
            "where": {"status": "active"},
            "group_by": "group",
            "sort": "priority",
            "limit": 5,
            "fields": ["id", "title"],
            "map": {"label": "{{ title }}"},
        })
        for expected in ("where status = active", "grouped by group", "sorted by priority",
                         "first 5", "keeping id, title", "renamed for the prefab"):
            self.assertIn(expected, text)

    def test_the_items_root_reads_as_the_whole_doc(self):
        self.assertEqual(self.phrase({"from": "design.$items"}), "every item in docs/design.yaml")

    def test_counts_say_how_many_of_how_many(self):
        text = self.phrase({"from": "plan.milestones", "where": {"status": "active"}}, counts=(4, 17))
        self.assertTrue(text.endswith("showing 4 of 17"), text)

    def test_an_unfiltered_count_does_not_pretend_to_be_a_selection(self):
        text = self.phrase({"from": "plan.milestones"}, counts=(17, 17))
        self.assertIn("17 item(s)", text)
        self.assertNotIn("showing 17 of 17", text)

    def test_a_hook_generated_source_is_named_not_offered_as_a_file(self):
        text = self.phrase({"from": "hooked.things"})
        self.assertIn("hooks.py:load_docs()[0]", text)

    def test_a_collection_local_id_resolves_to_the_collection_doc(self):
        text = describe({"from": "plan.milestones"}, DOCS, collection="c")
        self.assertIn("c/docs/plan.yaml", text)

    def test_a_leading_slash_forces_the_root_doc(self):
        text = describe({"from": "/plan.milestones"}, DOCS, collection="c")
        self.assertIn("docs/plan.yaml", text)
        self.assertNotIn("c/docs", text)


class VirtualRootTests(unittest.TestCase):
    def test_every_virtual_root_has_a_phrase(self):
        """`source_doc` returns None for all of them, so without this they would print nothing."""
        from yss.binding import resolve_from  # noqa: PLC0415

        roots = re.findall(r'"(\w+)": lambda', Path(REPO / "yss" / "binding.py").read_text(encoding="utf-8"))
        self.assertTrue(roots, "could not read the virtual root table from binding.py")
        for root in roots:
            with self.subTest(root=root):
                self.assertIn(root, VIRTUAL_ROOTS, f"${root} has no attribution phrase")
        self.assertTrue(callable(resolve_from))

    def test_a_virtual_root_describes_itself(self):
        self.assertEqual(describe({"from": "$docs"}, DOCS), VIRTUAL_ROOTS["docs"])

    def test_a_path_into_a_virtual_root_keeps_the_path(self):
        self.assertIn("name from site.yaml", describe({"from": "$site.name"}, DOCS))


class RedactionTests(unittest.TestCase):
    """The filter half is authored in page YAML and has never reached dist/."""

    SPEC = {"from": "plan.milestones", "where": {"owner": "a person's name"}, "limit": 3}

    def test_detail_off_keeps_the_source_and_drops_the_filter(self):
        text = describe(self.SPEC, DOCS, detail=False)
        self.assertIn("docs/plan.yaml", text)
        self.assertNotIn("a person's name", text)
        self.assertNotIn("where", text)
        self.assertNotIn("first 3", text)

    def test_detail_on_keeps_everything(self):
        text = describe(self.SPEC, DOCS, detail=True)
        self.assertIn("a person's name", text)


class HeadlineTests(unittest.TestCase):
    def attr(self, sec, **kw) -> Attribution:
        return attribute(sec, {"_source": "site/pages/x.yaml"}, DOCS, **kw)

    def test_one_doc_is_the_headline(self):
        a = self.attr({"type": "prefab", "args": {"items": {"from": "plan.milestones"}}})
        self.assertEqual(a.doc, "plan")
        self.assertEqual(a.src, "docs/plan.yaml")

    def test_two_lists_from_one_doc_stay_on_that_doc(self):
        a = self.attr({"type": "prefab", "args": {
            "a": {"from": "plan.milestones"}, "b": {"from": "plan.risks"}}})
        self.assertEqual(a.doc, "plan")
        self.assertNotIn("2 docs", a.text)

    def test_scalars_go_on_a_secondary_line_rather_than_being_dropped(self):
        """The code map page binds modules plus repo_url, commit and dirty."""
        a = self.attr({"type": "prefab", "args": {
            "modules": {"from": "plan.milestones"},
            "commit": {"from": "$build.commit"},
        }})
        self.assertEqual(a.doc, "plan")
        self.assertTrue(a.secondary, "a $build scalar should be reported, not discarded")
        self.assertIn("build", a.full())

    def test_several_docs_prefer_the_list_valued_binding(self):
        a = self.attr({"type": "prefab", "args": {
            "items": {"from": "plan.milestones"}, "blurb": {"from": "design.overview"}}})
        self.assertEqual(a.doc, "plan")

    def test_genuinely_ambiguous_names_them_all_and_stops(self):
        """Never silently pick one - a wrong attribution is worse than none."""
        a = self.attr({"type": "prefab", "args": {
            "a": {"from": "plan.milestones"}, "b": {"from": "design.components"}}})
        self.assertIn("2 docs", a.text)
        self.assertIn("docs/plan.yaml", a.text)
        self.assertIn("docs/design.yaml", a.text)
        self.assertIsNone(a.doc)


class FallbackTests(unittest.TestCase):
    def attr(self, sec) -> Attribution:
        return attribute(sec, {"_source": "site/pages/x.yaml"}, DOCS)

    def test_a_dynamic_section_names_its_live_source(self):
        a = self.attr({"type": "dynamic", "id": "s", "source": "testruns"})
        self.assertIn("live source `testruns`", a.text)

    def test_an_include_names_the_file(self):
        a = self.attr({"type": "include", "id": "s", "path": "README.md"})
        self.assertIn("README.md", a.text)
        self.assertEqual(a.src, "README.md")

    def test_an_embed_names_what_it_embeds(self):
        a = self.attr({"type": "embed", "id": "s", "src": "play/index.html", "kind": "godot"})
        self.assertIn("godot", a.text)

    def test_html_falls_back_to_the_page_that_authored_it(self):
        a = self.attr({"type": "html", "id": "notice", "html": "<p>hi</p>"})
        self.assertIn("site/pages/x.yaml", a.text)
        self.assertIn("notice", a.text)

    def test_literal_markdown_attributes_to_the_page(self):
        a = self.attr({"type": "markdown", "id": "about", "markdown": "hello"})
        self.assertIn("site/pages/x.yaml", a.text)

    def test_an_attribution_is_falsy_when_it_has_nothing_to_say(self):
        self.assertFalse(Attribution())
        self.assertTrue(Attribution(text="something"))


if __name__ == "__main__":
    unittest.main()

"""Regression tests for gh-27: a design item at `status: open` could not say what it was open between.

All six `doc.design` item arrays carry `status` from `claim_status`, so the schema could say an item
was undecided but not what the choices were - and `unevaluatedProperties: false` meant there was
nowhere to put them. They degraded to prose in `notes`: unenumerable, unrenderable, uncheckable and
invisible to any page binding. `alternatives[]` does not fill the gap; it requires
`rejected_because` and is a top-level array unattached to any item, so it models the closed case.

Placement is the load-bearing decision here, and it is *not* `item_base` despite `status` living
there. `doc.plan`'s `open_questions[].options` is an array of strings and `doc.worksheet`'s
`questions[].options[]` is an object requiring `value`. A JSON Schema `allOf` composes by
intersection, so an `item_base` declaration would also bind those and fail every worksheet and
every plan question in the repository - the same trap the envelope's note on `status` describes.
The shape is a shared `$defs/option` in the envelope, referenced by the kinds that want it.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

DESIGN_ARRAYS = ("principles", "components", "interfaces", "flows", "constraints", "alternatives")


class SchemaShapeTests(TempSiteCase):
    def registry(self):
        return load_all(self.cfg).registry

    def test_every_design_item_array_accepts_options(self):
        design = self.registry().schemas["doc.design"]
        for name in DESIGN_ARRAYS:
            with self.subTest(array=name):
                props = design["properties"][name]["items"]["properties"]
                self.assertIn("options", props, f"design.{name} cannot enumerate alternatives")

    def test_the_option_shape_is_shared_not_duplicated(self):
        design = self.registry().schemas["doc.design"]
        self.assertIn("option", design["$defs"], "the shape should come from the envelope's $defs")
        option = design["$defs"]["option"]
        self.assertEqual(option["required"], ["text"])
        self.assertEqual(set(option["properties"]), {"text", "why", "pros", "cons"})
        self.assertFalse(option.get("additionalProperties", True))

    def test_the_worksheet_keys_that_do_not_belong_are_absent(self):
        """`value` is for form submission; `recommended`/`prompt` steer a human towards an answer.
        A design item documents a live choice rather than asking for one."""
        option = self.registry().schemas["doc.design"]["$defs"]["option"]
        for key in ("value", "recommended", "prompt"):
            self.assertNotIn(key, option["properties"])

    def test_options_is_not_on_item_base(self):
        """Putting it there would intersect with two existing, incompatible `options` shapes."""
        env = self.registry().schemas["doc.envelope"]
        self.assertNotIn("options", env["$defs"]["item_base"]["properties"])


class ExistingDocsStillValidateTests(TempSiteCase):
    def test_plan_open_questions_keep_their_string_options(self):
        """doc.plan's open_questions[].options is an array of strings and must stay legal."""
        self.write_doc(
            "q.yaml",
            "kind: plan\ntitle: Q\nmilestones: []\n"
            "open_questions:\n"
            "  - {id: q1, question: 'Which way?', options: [left, right], owner: panda}\n",
        )
        loaded = load_all(self.cfg)
        self.assertEqual([e for e in loaded.errors if "q.yaml" in e], [])

    def test_the_shipped_worksheets_still_validate(self):
        """Four worksheets in docs/ use value/recommended/prompt on their options."""
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        worksheets = [d for d in loaded.docs.values() if d.get("kind") == "worksheet"]
        self.assertTrue(worksheets, "the fixture should carry worksheets")
        self.assertTrue(
            any("value" in o for w in worksheets for q in w["questions"] for o in (q.get("options") or [])),
            "the incompatible shape should actually be exercised",
        )


class ValidationTests(TempSiteCase):
    def design(self, options_block: str) -> list[str]:
        self.write_doc(
            "d2.yaml",
            "kind: design\ntitle: D2\ncomponents:\n"
            "  - id: c1\n    name: C\n    responsibility: Does a thing.\n    status: open\n"
            + options_block,
        )
        return [e for e in load_all(self.cfg).errors if "d2.yaml" in e]

    def test_a_minimal_option_is_enough(self):
        self.assertEqual(self.design("    options:\n      - {text: Do it one way.}\n"), [])

    def test_an_option_without_text_fails(self):
        errors = self.design("    options:\n      - {why: no text here}\n")
        self.assertTrue(any("text" in e for e in errors), errors)

    def test_an_unknown_option_key_fails(self):
        errors = self.design("    options:\n      - {text: A, verdict: chosen}\n")
        self.assertTrue(errors, "additionalProperties: false should reject it")


class RenderingTests(TempSiteCase):
    def test_the_pilot_design_page_renders_its_open_item_options(self):
        """`map:` starts from the whole item and only renames the keys it is given, so options
        reach the card with no page change at all - which is the point of the placement."""
        build(self.cfg, "private", run_dynamic=False)
        html = (self.root / "dist" / "private" / "design" / "index.html").read_text(encoding="utf-8")
        self.assertIn("card-options", html, "an open design item should show what it is open between")
        self.assertIn("2 options", html)
        self.assertIn("Extend the token set to every on-colour surface", html)
        self.assertIn("pros and cons", html)

    def test_the_repository_has_an_open_design_item_to_render(self):
        docs = load_all(self.cfg).docs
        openers = [i for i in docs["design"]["constraints"] if i.get("status") == "open"]
        self.assertTrue(openers, "the pilot should demonstrate the field it just added")
        self.assertTrue(all(i.get("options") for i in openers), "an open item with no options is unfinished")


if __name__ == "__main__":
    unittest.main()

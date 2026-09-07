"""Regression tests for gh-37: a worksheet had no publishable archived form.

`visibility` used to be an authored key read literally (`is_visible`), and redaction removed whole
objects plus the single hard-coded `private_notes`. A worksheet is written private because of its
candid free text, and that reason does not expire when the questions are answered - so an answered
sheet, which is exactly the artefact worth publishing, stayed private forever, and flipping the key
by hand would have published the candid parts along with the answers.

The fix adds a *derived* value, `visibility: on_resolution`, legal only on a `doc.worksheet`: the
sheet is private while any question that requires an answer lacks a `resolution`, and once resolved
it publishes a **projection** of itself - structured answers, not prose. What is elided is stated in
`yss/visibility.py`'s module docstring and pinned below: `resolution.summary`, `agent_notes` and (as
before) `private_notes`, with a `kind: text` answer reduced to the `resolution.answered` marker.

The last two classes are the ones that matter for CLAUDE.md's "nothing private reaches dist/": the
elided strings are asserted absent from every byte of the public output tree, not merely from the
in-memory document, because `data/docs/<id>.json` is written for every visible doc whether or not a
page binds it.

Run with: python -m unittest discover -s tests -p "test_gh37.py" -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402
from yss.visibility import filter_for_target, is_visible, worksheet_resolved  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

SUMMARY = "Panda-only steering prose that must never be published"
NOTE = "candid private note about the reviewer"
AGENT_NOTE = "agent-only instruction that must never be published"


def sheet(visibility: str, *, resolved: bool, text_answered: bool | None = True) -> str:
    """A two-question worksheet: one option question, one free-text question."""
    text_res = ""
    if resolved:
        text_res = (
            f"    resolution:\n"
            f"      summary: {SUMMARY} (text)\n"
        )
        if text_answered is not None:
            text_res += f"      answered: {'true' if text_answered else 'false'}\n"
    opt_res = (
        f"    resolution:\n"
        f"      summary: {SUMMARY} (option)\n"
        f"      chose: [keep]\n"
        f"      applied: 2026-09-05\n"
        f"      by: panda\n"
        if resolved
        else ""
    )
    return (
        "kind: worksheet\n"
        "title: Sheet\n"
        f"visibility: {visibility}\n"
        f"agent_notes: {AGENT_NOTE}\n"
        f"private_notes: {NOTE}\n"
        "prompt:\n"
        "  task: Apply the verdicts.\n"
        "  steps: [Edit the docs.]\n"
        "questions:\n"
        "  - id: q-keep\n"
        "    call: C.1\n"
        "    lead: Keep the parser?\n"
        "    options:\n"
        "      - {value: keep, text: Keep it, recommended: true}\n"
        "      - {value: drop, text: Drop it}\n"
        + opt_res
        + "  - id: q-why\n"
        "    lead: Anything else?\n"
        "    kind: text\n"
        + text_res
    )


class DerivedVisibilityTests(TempSiteCase):
    def load(self, text: str, name: str = "ws37.yaml"):
        self.write_doc(name, text)
        loaded = load_all(self.cfg)
        self.assertEqual([e for e in loaded.errors if name in e], [], loaded.errors)
        return loaded

    def test_an_unresolved_sheet_is_private_and_a_resolved_one_is_public(self):
        loaded = self.load(sheet("on_resolution", resolved=False))
        doc = loaded.docs["ws37"]
        self.assertFalse(worksheet_resolved(doc))
        self.assertFalse(is_visible(doc, "public"))
        self.assertTrue(is_visible(doc, "private"))

        loaded = self.load(sheet("on_resolution", resolved=True))
        doc = loaded.docs["ws37"]
        self.assertTrue(worksheet_resolved(doc))
        self.assertTrue(is_visible(doc, "public"))

    def test_an_optional_question_does_not_hold_the_sheet_closed(self):
        text = sheet("on_resolution", resolved=False).replace(
            "  - id: q-why\n    lead: Anything else?\n    kind: text\n",
            "  - id: q-why\n    lead: Anything else?\n    kind: text\n    required: false\n",
        ).replace(
            "      - {value: drop, text: Drop it}\n",
            "      - {value: drop, text: Drop it}\n"
            "    resolution: {summary: done, chose: [keep]}\n",
        )
        loaded = self.load(text)
        self.assertTrue(worksheet_resolved(loaded.docs["ws37"]))

    def test_an_empty_sheet_never_flips(self):
        self.assertFalse(worksheet_resolved({"kind": "worksheet", "questions": []}))

    def test_the_keyword_fails_closed_off_a_worksheet(self):
        self.assertFalse(is_visible({"kind": "plan", "visibility": "on_resolution"}, "public"))

    def test_literal_public_and_private_are_unchanged(self):
        loaded = self.load(sheet("private", resolved=True))
        self.assertFalse(is_visible(loaded.docs["ws37"], "public"))
        loaded = self.load(sheet("public", resolved=False))
        doc = loaded.docs["ws37"]
        self.assertTrue(is_visible(doc, "public"))
        # A literally-public sheet is published as authored: nothing is projected away but the
        # `private_notes` that always went.
        pub = filter_for_target(doc, "public")
        self.assertEqual(pub["questions"][0]["options"][0]["value"], "keep")
        self.assertNotIn("private_notes", pub)
        self.assertEqual(pub.get("agent_notes"), AGENT_NOTE)

    def test_the_schema_rejects_the_keyword_on_another_kind(self):
        self.write_doc("p37.yaml", "kind: plan\ntitle: P\nvisibility: on_resolution\nmilestones: []\n")
        loaded = load_all(self.cfg)
        self.assertTrue(
            any("p37.yaml" in e and "visibility" in e for e in loaded.errors), loaded.errors
        )


class ProjectionTests(TempSiteCase):
    def projected(self) -> dict:
        self.write_doc("ws37.yaml", sheet("on_resolution", resolved=True))
        loaded = load_all(self.cfg)
        self.assertEqual([e for e in loaded.errors if "ws37" in e], [], loaded.errors)
        return filter_for_target(loaded.docs["ws37"], "public")

    def test_the_public_form_keeps_the_answers_and_drops_the_prose(self):
        pub = self.projected()
        option, text = pub["questions"]
        self.assertEqual(option["resolution"]["chose"], ["keep"])
        self.assertEqual(option["resolution"]["applied"], "2026-09-05")
        self.assertEqual(option["resolution"]["by"], "panda")
        self.assertNotIn("summary", option["resolution"])
        self.assertNotIn("summary", text["resolution"])
        self.assertEqual(text["resolution"]["answered"], True)
        # the options as offered, and the prompt, are the point of publishing
        self.assertEqual([o["value"] for o in option["options"]], ["keep", "drop"])
        self.assertEqual(pub["prompt"]["task"], "Apply the verdicts.")
        self.assertNotIn("agent_notes", pub)
        self.assertNotIn("private_notes", pub)
        # stamped public, so projecting a projection changes nothing
        self.assertEqual(pub["visibility"], "public")
        self.assertEqual(filter_for_target(pub, "public"), pub)

    def test_the_private_target_still_gets_the_authored_document(self):
        self.write_doc("ws37.yaml", sheet("on_resolution", resolved=True))
        loaded = load_all(self.cfg)
        priv = filter_for_target(loaded.docs["ws37"], "private")
        self.assertEqual(priv["questions"][0]["resolution"]["summary"], f"{SUMMARY} (option)")
        self.assertEqual(priv["agent_notes"], AGENT_NOTE)
        self.assertEqual(priv["private_notes"], NOTE)


class ValidationTests(TempSiteCase):
    def errors_for(self, text: str) -> list[str]:
        self.write_doc("ws37.yaml", text)
        return [e for e in load_all(self.cfg).errors if "ws37" in e]

    def test_a_chose_value_naming_no_option_fails_validation(self):
        errors = self.errors_for(
            sheet("on_resolution", resolved=True).replace("chose: [keep]", "chose: [kepp]")
        )
        self.assertTrue(errors, "a typo'd chose[] must fail validation")
        self.assertIn("kepp", errors[0])
        self.assertIn("q-keep", errors[0])

    def test_the_check_applies_to_a_plain_worksheet_too(self):
        errors = self.errors_for(
            sheet("public", resolved=True).replace("chose: [keep]", "chose: [nope]")
        )
        self.assertTrue(any("nope" in e for e in errors), errors)

    def test_a_resolved_text_question_needs_the_marker(self):
        errors = self.errors_for(
            sheet("on_resolution", resolved=True, text_answered=None)
        )
        self.assertTrue(any("answered" in e and "q-why" in e for e in errors), errors)
        # ... and only on a derived-visibility sheet, where the marker is the public answer
        self.assertEqual(self.errors_for(sheet("private", resolved=True, text_answered=None)), [])


class PublicBuildTests(TempSiteCase):
    """The surface that actually decides what is published: the output tree, byte for byte."""

    PAGE = (
        "id: ws37\nroute: /ws37/\ntitle: Sheet\nvisibility: public\n"
        "docs: [ws37]\n"
        "sections:\n"
        "  - id: worksheet\n    type: prefab\n    prefab: worksheet\n    args:\n"
        "      title: {from: ws37.title}\n"
        "      questions: {from: ws37.questions}\n"
        "      prompt: {from: ws37.prompt}\n"
    )

    def build_both(self, doc_text: str, with_page: bool = False):
        self.write_doc("ws37.yaml", doc_text)
        if with_page:
            (self.root / "site" / "pages" / "ws37.yaml").write_text(self.PAGE, encoding="utf-8")
        loaded = load_all(self.cfg)
        self.assertEqual([e for e in loaded.errors if "ws37" in e], [], loaded.errors)
        pub = build(self.cfg, "public", out_dir=self.root / "out-pub", run_dynamic=False, loaded=loaded)
        priv = build(self.cfg, "private", out_dir=self.root / "out-priv", run_dynamic=False, loaded=loaded)
        return pub, priv

    def tree_text(self, out_dir: Path) -> str:
        chunks = []
        for path in sorted(out_dir.rglob("*")):
            if path.is_file():
                chunks.append(path.read_text(encoding="utf-8", errors="ignore"))
        return "\n".join(chunks)

    def test_an_unresolved_sheet_reaches_the_private_tree_only(self):
        pub, priv = self.build_both(sheet("on_resolution", resolved=False))
        self.assertNotIn("ws37", pub.docs)
        self.assertIn("ws37", priv.docs)
        self.assertFalse((pub.out_dir / "data" / "docs" / "ws37.json").exists())
        self.assertTrue((priv.out_dir / "data" / "docs" / "ws37.json").exists())
        self.assertNotIn(SUMMARY, self.tree_text(pub.out_dir))

    def test_a_resolved_sheet_publishes_its_answers_and_nothing_else(self):
        pub, priv = self.build_both(sheet("on_resolution", resolved=True), with_page=True)
        self.assertIn("ws37", pub.docs)
        exported = (pub.out_dir / "data" / "docs" / "ws37.json").read_text(encoding="utf-8")
        self.assertIn('"chose"', exported)
        self.assertIn('"answered"', exported)

        public_bytes = self.tree_text(pub.out_dir)
        for elided in (SUMMARY, NOTE, AGENT_NOTE):
            self.assertNotIn(elided, public_bytes, f"{elided!r} reached the public output tree")
        # the private tree is where the candid material still lives
        private_bytes = self.tree_text(priv.out_dir)
        for kept in (SUMMARY, NOTE, AGENT_NOTE):
            self.assertIn(kept, private_bytes)

    def test_the_published_page_reads_as_a_record_with_no_empty_sections(self):
        pub, _ = self.build_both(sheet("on_resolution", resolved=True), with_page=True)
        html = (pub.out_dir / "ws37" / "index.html").read_text(encoding="utf-8")
        self.assertIn("worksheet-record", html)            # the record banner
        self.assertIn("Keep the parser?", html)            # the question
        self.assertIn("Keep it", html)                     # the chosen option's label
        self.assertIn("&lt;filled&gt;", html)              # the text answer's marker
        self.assertNotIn("What addressed it", html)        # no heading over elided prose
        self.assertNotIn("Build the instruction", html)    # a record offers no form
        self.assertNotIn(SUMMARY, html)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()

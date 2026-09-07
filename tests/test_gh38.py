"""Regression tests for gh-38: references inside code spans are invisible to the validator.

`_check_inline` runs over `strip_code(text)`, so a reference in backticks is never checked;
`expand_refs` then emits it verbatim. A reader sees literal text where a link was intended, and
`validate`, `--strict` and `check` all pass. `check_code_span_refs` closes the reporting gap *as a
warning*, because quoting the syntax in backticks is a legitimate and common thing to do - the
pilot's own docs do it twice on purpose, one of them inside an append-only ADR that must not be
edited.

The discriminator is resolvability: warn only when the reference would have been a live link (the
doc exists, and the item, if named, exists too). A backticked ref to a doc or item that does not
exist is what quoting the syntax looks like, and stays silent.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.loader import check_code_span_refs  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

TARGET_DOC = """id: gh38ref
kind: generic
title: GH38 target
data:
  - id: alpha
    title: Alpha
"""


def doc(note: str) -> str:
    return f"""id: gh38
kind: generic
title: GH38
data:
  note: {note}
"""


class SignatureTests(unittest.TestCase):
    def test_the_agreed_signature(self):
        """The CLI/build wiring is written against this exact shape."""
        self.assertEqual(list(inspect.signature(check_code_span_refs).parameters), ["docs", "pages"])


class WarningCase(TempSiteCase):
    """Self-contained fixture: `gh38ref` is the doc `gh38` points at, so no test here depends on
    the pilot's own ids staying put."""

    def warnings_for(self, body: str) -> list[str]:
        self.write_doc("gh38ref.yaml", TARGET_DOC)
        self.write_doc("gh38.yaml", body)
        loaded = load_all(Config.load(self.root))
        self.assertEqual(loaded.errors, [], "the fixture itself must validate")
        return [w for w in check_code_span_refs(loaded.docs, loaded.pages) if "gh38.yaml" in w]


class LiveLinkTests(WarningCase):
    def test_a_backticked_ref_whose_doc_and_item_both_exist_is_warned(self):
        found = self.warnings_for(doc('"see `[[gh38ref#alpha]]` here"'))
        self.assertEqual(len(found), 1, found)
        self.assertIn("[[gh38ref#alpha]]", found[0])
        self.assertIn("code span", found[0])
        self.assertIn("data/note", found[0])

    def test_a_backticked_doc_only_ref_is_warned(self):
        self.assertEqual(len(self.warnings_for(doc('"see `[[gh38ref]]` here"'))), 1)

    def test_a_bare_item_ref_resolves_against_the_owning_doc(self):
        self.write_doc("gh38ref.yaml", TARGET_DOC)
        self.write_doc("gh38.yaml", 'id: gh38\nkind: generic\ntitle: GH38\n'
                                    'data:\n  - id: beta\n    title: "see `[[#beta]]`"\n')
        loaded = load_all(Config.load(self.root))
        self.assertEqual(loaded.errors, [])
        found = [w for w in check_code_span_refs(loaded.docs, loaded.pages) if "gh38.yaml" in w]
        self.assertEqual(len(found), 1, found)

    def test_a_fenced_block_counts_as_a_code_span_too(self):
        self.assertEqual(len(self.warnings_for(doc('"before ```[[gh38ref#alpha]]``` after"'))), 1)


class SilenceTests(WarningCase):
    def test_a_backticked_ref_to_a_nonexistent_doc_is_not_warned(self):
        """This is what quoting the syntax looks like; it renders literally and that is correct."""
        self.assertEqual(self.warnings_for(doc('"syntax is `[[nosuchdoc#item]]`"')), [])

    def test_a_real_doc_with_a_bogus_item_is_not_warned(self):
        self.assertEqual(self.warnings_for(doc('"syntax is `[[gh38ref#no-such-item]]`"')), [])

    def test_an_unbackticked_reference_is_not_warned(self):
        """A live link is the thing we are asking for; the validator already covers it."""
        self.assertEqual(self.warnings_for(doc('"see [[gh38ref#alpha]]"')), [])

    def test_a_code_span_with_no_reference_at_all_is_not_warned(self):
        self.assertEqual(self.warnings_for(doc('"run `python -m yss validate`"')), [])


class NonFatalTests(WarningCase):
    def test_the_warning_never_becomes_a_validation_error(self):
        """Never an error and never strict-fatal: the pilot ships two of these on purpose.

        `warnings_for` asserts `loaded.errors == []` on the very doc that gets warned about.
        """
        self.assertTrue(self.warnings_for(doc('"see `[[gh38ref#alpha]]` here"')))


class PilotTests(unittest.TestCase):
    """The repository is its own fixture: two intentional resolving refs live in backticks."""

    def setUp(self):
        self.loaded = load_all(Config.load(REPO))
        self.warnings = check_code_span_refs(self.loaded.docs, self.loaded.pages)

    def test_the_two_known_intentional_cases_are_reported(self):
        joined = "\n".join(self.warnings)
        self.assertIn("[[audit#arch-serve-stale-config]]", joined)
        self.assertIn("[[design#g-sources]]", joined)

    def test_the_pilot_still_validates_clean(self):
        self.assertEqual(self.loaded.errors, [])


if __name__ == "__main__":
    unittest.main()

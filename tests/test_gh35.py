"""Regression tests for gh-35: `options[]` could not carry evidence.

gh-27 gave every `doc.design` item array an `options[]` so an item at `status: open` could say what
it was open between. The shape it got - the envelope's `$defs/option` - was
`additionalProperties: false` over `{text, why, pros, cons}`, so the one thing an alternative most
often needs to say ("we already tried this", "that file does not exist yet") had nowhere to go but
prose. Meanwhile the six `options:` use sites in `doc.design` each repeated the same five-line
description verbatim while the real shape sat in `$defs` a hundred lines below, so the duplicated
prose was the *only* documentation a reader met and it said nothing about the fields.

The fix adds `evidence[]` and `notes` to `$defs/option` and collapses the six descriptions to a
pointer at `$defs/option`, where the prose now lives once.

Nothing in `collect_claims` needed changing, and this file exists to hold that to it: the walk
already descends into any dict with an `evidence:` key and inherits the nearest enclosing `id`, so
an option's claim reports under its *parent item* with a field path that names the option. That
inheritance is the reason an option is not its own reportable unit, and the assertions below pin
both halves - the id it borrows and the pointer that says which option spoke.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.evidence import check  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

DESIGN_HEAD = "kind: design\ntitle: D\noverview: Ov.\ncomponents:\n"


class OptionEvidenceTests(TempSiteCase):
    def design(self, options: str) -> tuple[list[str], dict]:
        self.write_doc(
            "d2.yaml",
            DESIGN_HEAD
            + "  - id: c1\n    name: C\n    responsibility: Does a thing.\n    status: open\n"
            + options,
        )
        loaded = load_all(self.cfg)
        return [e for e in loaded.errors if "d2.yaml" in e], loaded

    def test_an_option_may_carry_evidence_and_notes(self):
        errors, _ = self.design(
            "    options:\n"
            "      - text: Keep the parser.\n"
            "        why: Nothing else reads the file.\n"
            "        notes: Measured on the pilot repo.\n"
            "        evidence:\n"
            "          - {path: yss/evidence.py, contains: 'def collect_claims('}\n"
        )
        self.assertEqual(errors, [])

    def test_an_option_claim_is_collected_under_its_parent_item(self):
        """The enclosing item's id travels down the walk, so the claim is attributable."""
        errors, loaded = self.design(
            "    options:\n"
            "      - text: Keep the parser.\n"
            "        evidence:\n"
            "          - {path: yss/evidence.py, contains: 'def collect_claims('}\n"
            "          - {path: yss/evidence.py, contains: 'no such text at all'}\n"
        )
        self.assertEqual(errors, [])
        report = check(self.cfg, {"d2": loaded.docs["d2"]}, loaded.registry,
                       run_commands=False, git_recency=False)
        claims = {c.field: c for c in report.claims}

        first = "components/0/options/0/evidence/0"
        second = "components/0/options/0/evidence/1"
        self.assertIn(first, claims, sorted(claims))
        self.assertEqual(claims[first].item, "c1", "an option borrows the enclosing item's id")
        self.assertEqual(claims[first].status, "ok")
        self.assertEqual(claims[second].status, "stale")

        summary = report.summary()["d2"]
        self.assertEqual(summary["items"]["c1"], "stale", "the parent item wears its option's failure")

    def test_option_evidence_reaches_the_new_kinds_too(self):
        """gh-32's forms are on `$defs/evidence_item`, which is what an option references."""
        errors, loaded = self.design(
            "    options:\n"
            "      - text: Drop the flag.\n"
            "        evidence:\n"
            "          - {path: yss/evidence.py, not_contains: 'zzz-never-written'}\n"
            "          - {path: docs/plan.yaml, at: 'kind', equals: plan}\n"
        )
        self.assertEqual(errors, [])
        report = check(self.cfg, {"d2": loaded.docs["d2"]}, loaded.registry,
                       run_commands=False, git_recency=False)
        claims = {c.field: c for c in report.claims}
        self.assertEqual(claims["components/0/options/0/evidence/0"].kind, "not_contains")
        self.assertEqual(claims["components/0/options/0/evidence/0"].status, "ok")
        self.assertEqual(claims["components/0/options/0/evidence/1"].kind, "at")
        self.assertEqual(claims["components/0/options/0/evidence/1"].status, "ok")

    def test_an_unknown_option_key_still_fails(self):
        """gh-27's guard: widening the shape must not become a free-for-all."""
        errors, _ = self.design("    options:\n      - {text: A, verdict: chosen}\n")
        self.assertTrue(errors, "additionalProperties: false should still reject it")


class SchemaDiscoverabilityTests(TempSiteCase):
    """gh-35 part 1 - the reported symptom was that `$defs.option` never reached the printed
    kind schema. It does, and has since the envelope fold copied every `$def` into every kind;
    these assertions keep that true rather than fix anything."""

    def registry(self):
        return load_all(self.cfg).registry

    def test_the_folded_design_schema_carries_the_option_def(self):
        design = self.registry().schemas["doc.design"]
        self.assertIn("option", design["$defs"], "the envelope fold must reach $defs")
        self.assertIn("evidence", design["$defs"]["option"]["properties"])
        self.assertIn("notes", design["$defs"]["option"]["properties"])

    def test_the_six_use_sites_point_at_the_def_instead_of_repeating_it(self):
        design = self.registry().schemas["doc.design"]
        arrays = ("principles", "components", "interfaces", "flows", "constraints", "alternatives")
        for name in arrays:
            options = design["properties"][name]["items"]["properties"]["options"]
            self.assertEqual(options["items"]["$ref"], "#/$defs/option", name)
            self.assertIn("$defs/option", options["description"], name)
            self.assertLess(len(options["description"]), 120, f"{name}: the prose belongs on the $def")


if __name__ == "__main__":
    unittest.main()

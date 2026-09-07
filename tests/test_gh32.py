"""Regression tests for gh-32: evidence had no negative form and no within-doc anchor.

Every `path` claim was a whole-file substring test. Two things follow from that, and both were
observed rather than theorised:

  1. Nothing could prove a *removal*. "The workaround is gone", "no TODO is left in this module",
     "the flag no longer appears" are the claims you most want checked after a cleanup, and a
     positive `contains` cannot express any of them.
  2. A claim about a value could be satisfied by an unrelated line. The reported symptom: a claim
     written as `{path: docs/design.yaml, contains: "status: open"}`, to track a defect until the
     item closed, matched the *document's own* top-level `status:` key and reported `ok` forever.
     The claim was structurally incapable of failing, which is worse than no claim at all.

The fix adds `not_contains:` for the first, an anchored `at:` form (`equals` / `in` / `absent`)
that parses the YAML and judges the values rather than the bytes for the second, and - because a
five-year-old `contains` claim will not rewrite itself - a `warn` on exactly the case where a
`key: value` needle matched nothing but top-level lines.

Every failure mode of `at:` is a `stale` with a reason. `yss check` walks every doc in the
repository, so one malformed claim must never take the run down with it; the tests below include a
target that is not YAML, a file that will not parse, and an anchor that resolves to nothing.

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


class EvidenceCase(TempSiteCase):
    """One generic doc whose `evidence:` list is the thing under test."""

    def claims(self, evidence: str) -> dict:
        self.write_doc("x.yaml", "kind: generic\ntitle: X\ndata: {}\nevidence:\n" + evidence)
        loaded = load_all(self.cfg)
        self.assertEqual([e for e in loaded.errors if "x.yaml" in e], [], "the doc itself must validate")
        report = check(self.cfg, {"x": loaded.docs["x"]}, loaded.registry,
                       run_commands=False, git_recency=False)
        return {c.field: c for c in report.claims}

    def target(self, name: str, body: str) -> None:
        (self.root / "docs" / name).write_text(body, encoding="utf-8")


SUBJECT = """kind: generic
title: Subject
status: open
data: {}
components:
  - {id: a, status: live}
  - {id: b, status: live}
"""


class NotContainsTests(EvidenceCase):
    def test_a_removed_string_passes_and_a_present_one_is_stale(self):
        """The negative form is the only way to prove a cleanup actually happened."""
        self.target("subject.yaml", SUBJECT)
        claims = self.claims(
            "  - {path: docs/subject.yaml, not_contains: 'status: dropped'}\n"
            "  - {path: docs/subject.yaml, not_contains: 'status: live'}\n"
        )
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.assertEqual(claims["evidence/0"].kind, "not_contains")
        self.assertEqual(claims["evidence/1"].status, "stale")

    def test_the_stale_detail_names_the_offending_file(self):
        """With a glob the claim covers many files; the reason has to say which one broke it."""
        self.target("subject.yaml", SUBJECT)
        claims = self.claims("  - {path: 'docs/subject.yaml', not_contains: 'status: live'}\n")
        detail = claims["evidence/0"].detail
        self.assertIn("docs/subject.yaml", detail)
        self.assertNotIn(str(self.root), detail, "no absolute path may reach a claim detail")

    def test_a_missing_path_is_still_stale(self):
        """A negative claim about a file that is gone proves nothing, so it does not pass."""
        claims = self.claims("  - {path: docs/nope.yaml, not_contains: anything}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("path not found", claims["evidence/0"].detail)


class AnchoredTests(EvidenceCase):
    def setUp(self):
        super().setUp()
        self.target("subject.yaml", SUBJECT)

    def test_equals_requires_every_leaf_to_match(self):
        claims = self.claims(
            "  - {path: docs/subject.yaml, at: 'components[*].status', equals: live}\n"
            "  - {path: docs/subject.yaml, at: 'components[*].status', equals: open}\n"
        )
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.assertEqual(claims["evidence/0"].kind, "at")
        self.assertEqual(claims["evidence/1"].status, "stale")
        self.assertIn("2 of 2", claims["evidence/1"].detail)

    def test_one_bad_leaf_out_of_several_is_stale(self):
        """`[*]` is a universal, not an existential: one live component does not excuse a dead one."""
        self.target("subject.yaml", SUBJECT.replace("{id: b, status: live}", "{id: b, status: open}"))
        claims = self.claims("  - {path: docs/subject.yaml, at: 'components[*].status', equals: live}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("1 of 2", claims["evidence/0"].detail)

    def test_in_accepts_a_set_of_values(self):
        self.target("subject.yaml", SUBJECT.replace("{id: b, status: live}", "{id: b, status: decided}"))
        claims = self.claims(
            "  - {path: docs/subject.yaml, at: 'components[*].status', in: [live, decided]}\n"
            "  - {path: docs/subject.yaml, at: 'components[*].status', in: [live]}\n"
        )
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.assertEqual(claims["evidence/1"].status, "stale")

    def test_absent_passes_only_while_the_anchor_resolves_to_nothing(self):
        """The claim a defect wants: 'no item is open any more'."""
        claims = self.claims("  - {path: docs/subject.yaml, at: 'flows[*].id', absent: true}\n")
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.target("subject.yaml", SUBJECT + "flows:\n  - {id: f1}\n")
        claims = self.claims("  - {path: docs/subject.yaml, at: 'flows[*].id', absent: true}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("f1", claims["evidence/0"].detail)

    def test_indexed_and_dotted_steps_resolve(self):
        claims = self.claims(
            "  - {path: docs/subject.yaml, at: 'components[1].status', equals: live}\n"
            "  - {path: docs/subject.yaml, at: 'title', equals: Subject}\n"
        )
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.assertEqual(claims["evidence/1"].status, "ok")

    def test_a_missing_key_is_stale_with_a_reason_not_a_crash(self):
        claims = self.claims("  - {path: docs/subject.yaml, at: 'nowhere[*].status', equals: live}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("resolves to nothing", claims["evidence/0"].detail)

    def test_a_non_yaml_target_is_stale_with_a_reason(self):
        claims = self.claims("  - {path: yss/cli.py, at: 'components[*].status', equals: live}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("not YAML", claims["evidence/0"].detail)

    def test_an_unparsable_file_is_stale_with_a_reason(self):
        self.target("broken.yaml", "kind: generic\n  bad: [indent\n")
        claims = self.claims("  - {path: docs/broken.yaml, at: 'kind', equals: generic}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("cannot parse", claims["evidence/0"].detail)

    def test_a_malformed_anchor_is_stale_with_a_reason(self):
        claims = self.claims("  - {path: docs/subject.yaml, at: 'components[*.status', equals: live}\n")
        self.assertEqual(claims["evidence/0"].status, "stale")
        self.assertIn("cannot parse at:", claims["evidence/0"].detail)


class EnvelopeSelfMatchTests(EvidenceCase):
    def test_a_contains_needle_matching_only_the_envelope_warns(self):
        """The reported symptom, made visible: this claim could never have failed."""
        self.target("subject.yaml", SUBJECT)
        claims = self.claims("  - {path: docs/subject.yaml, contains: 'status: open'}\n")
        self.assertEqual(claims["evidence/0"].status, "warn")
        self.assertIn("envelope's own 'status'", claims["evidence/0"].detail)
        self.assertIn("consider at:", claims["evidence/0"].detail)

    def test_a_needle_that_also_matches_an_item_stays_ok(self):
        """Only the provably-vacuous case warns; a claim that reaches an item is a real claim."""
        self.target("subject.yaml", SUBJECT.replace("{id: b, status: live}", "{id: b, status: open}"))
        claims = self.claims("  - {path: docs/subject.yaml, contains: 'status: open'}\n")
        self.assertEqual(claims["evidence/0"].status, "ok")

    def test_a_non_yaml_target_and_a_prose_needle_are_untouched(self):
        """The trap is specific to `key: value` against YAML; ordinary claims behave as before."""
        claims = self.claims(
            "  - {path: yss/cli.py, contains: 'def main('}\n"
            "  - {path: yss/cli.py, contains: 'no such text'}\n"
        )
        self.assertEqual(claims["evidence/0"].status, "ok")
        self.assertEqual(claims["evidence/0"].kind, "contains")
        self.assertEqual(claims["evidence/1"].status, "stale")


class SchemaTests(TempSiteCase):
    def errors(self, evidence: str) -> list[str]:
        self.write_doc("x.yaml", "kind: generic\ntitle: X\ndata: {}\nevidence:\n" + evidence)
        return [e for e in load_all(self.cfg).errors if "x.yaml" in e]

    def test_at_without_path_is_rejected(self):
        self.assertTrue(self.errors("  - {glob: 'docs/*.yaml', at: 'kind'}\n"))

    def test_equals_without_at_is_rejected(self):
        self.assertTrue(self.errors("  - {path: docs/plan.yaml, equals: live}\n"))

    def test_in_and_absent_without_at_are_rejected(self):
        self.assertTrue(self.errors("  - {path: docs/plan.yaml, in: [live]}\n"))
        self.assertTrue(self.errors("  - {path: docs/plan.yaml, absent: true}\n"))

    def test_the_new_keys_validate_when_used_correctly(self):
        self.assertEqual(self.errors("  - {path: docs/plan.yaml, not_contains: 'zzz'}\n"), [])
        self.assertEqual(self.errors("  - {path: docs/plan.yaml, at: 'kind', equals: plan}\n"), [])
        self.assertEqual(self.errors("  - {path: docs/plan.yaml, at: 'x[*].y', in: [a, b]}\n"), [])
        self.assertEqual(self.errors("  - {path: docs/plan.yaml, at: 'x[*].y', absent: true}\n"), [])

    def test_an_unknown_evidence_key_still_fails(self):
        self.assertTrue(self.errors("  - {path: docs/plan.yaml, lacks: 'zzz'}\n"))


if __name__ == "__main__":
    unittest.main()

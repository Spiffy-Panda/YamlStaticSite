"""Regression tests for gh-33: strict mode's stale-evidence branch printed a count, not the hits.

Its three sibling gates - dead references, dead links, flagged strings - all inline what they
found, so a strict build tells you exactly what to fix. The fourth said `3 stale evidence
claim(s); run \\`yss check\\`` and stopped, which is a number and a second command, not a diagnosis.
In CI that difference is the whole message: nobody can run `yss check` against a job that has
already exited.

Run with: python -m unittest tests.test_gh33 -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
# Also importable as `tests.test_gh__` (not only via `unittest discover -s tests`), so the
# sibling helper import below resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss import build as buildmod  # noqa: E402
from yss.build import BuildError, build  # noqa: E402
from yss.evidence import Claim, EvidenceReport  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class StrictStaleEvidenceTests(TempSiteCase):
    def test_strict_names_the_doc_field_and_target_of_a_stale_claim(self):
        """A `contains:` that cannot match must reach the operator by name, not as a tally."""
        self.write_doc(
            "gh33.yaml",
            "kind: generic\n"
            "title: GH33\n"
            "evidence:\n"
            "  - {path: yss/build.py, contains: no-such-needle-gh33}\n"
            "data: {}\n",
        )
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", strict=True, run_dynamic=False)
        message = str(ctx.exception)
        self.assertIn("strict mode", message)
        self.assertIn("stale evidence claim(s)", message)
        self.assertIn("gh33", message, message)  # the doc id
        self.assertIn("evidence/0", message, message)  # the field pointer
        self.assertIn("yss/build.py", message, message)  # the target
        # And the bare "run `yss check`" hand-off is gone when everything is shown inline.
        self.assertNotIn("claim(s); run", message, message)

    def test_the_warning_and_the_strict_message_use_one_format(self):
        """The line a non-strict build warns with is the line strict fails with (gh-33)."""
        self.write_doc(
            "gh33b.yaml",
            "kind: generic\n"
            "title: GH33B\n"
            "evidence:\n"
            "  - {path: yss/build.py, contains: no-such-needle-gh33b}\n"
            "data: {}\n",
        )
        report = build(self.cfg, "private", strict=False, run_dynamic=False)
        stale_warnings = [w[len("stale: ") :] for w in report.warnings if w.startswith("stale: ") and "gh33b" in w]
        self.assertTrue(stale_warnings, report.warnings)
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", strict=True, run_dynamic=False)
        for line in stale_warnings:
            self.assertIn(line, str(ctx.exception))


class StaleCapTests(TempSiteCase):
    """The 50-line cap and its tail.

    Authoring 51 genuinely failing claims in YAML would cost a doc nobody wants to read to prove
    a slice, so the evidence pass is replaced wholesale and the *real* build message inspected.
    """

    @staticmethod
    def _synthesise(n: int) -> EvidenceReport:
        return EvidenceReport(
            claims=[
                Claim(doc="synthetic", item=None, field=f"evidence/{i}", kind="contains",
                      target="yss/build.py", status="stale", detail="does not contain 'x'")
                for i in range(n)
            ]
        )

    def _strict_message(self, n: int) -> str:
        report = self._synthesise(n)
        with mock.patch.object(buildmod, "evidence_check", return_value=report):
            with self.assertRaises(BuildError) as ctx:
                build(self.cfg, "private", strict=True, run_dynamic=False)
        return str(ctx.exception)

    def test_a_51st_claim_is_summarised_rather_than_dropped_silently(self):
        message = self._strict_message(51)
        self.assertIn("51 stale evidence claim(s)", message)
        self.assertIn("synthetic evidence/49 -> yss/build.py", message)
        self.assertIn("... and 1 more; run `yss check`", message)
        self.assertNotIn("synthetic evidence/50 ", message)

    def test_exactly_fifty_claims_are_all_shown_with_no_tail(self):
        message = self._strict_message(50)
        self.assertIn("50 stale evidence claim(s)", message)
        self.assertIn("synthetic evidence/49 -> yss/build.py", message)
        self.assertNotIn("more; run `yss check`", message)


if __name__ == "__main__":
    unittest.main()

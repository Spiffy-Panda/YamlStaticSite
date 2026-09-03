"""Regression tests for GH-7: item_base gains an optional generic `status` field.

The design landed on: item_base.status stays an open string (no x-vocab, no enum) because a
JSON Schema `allOf` composes by intersection - giving item_base.status its own enum would also
constrain every item that already has a kind-specific status (plan milestones/tasks: work_status,
risks: risk_status, questions: question_status, design components / codemap modules: work_status,
decision entries: record_status), and their existing values would stop validating. Kinds that want
a real, enum-checked status for items that previously had none (design principles, interfaces,
flows, constraints, alternatives) declare it locally with `x-vocab: claim_status`
(live, decided, open, superseded - config.py DEFAULTS["vocabularies"]["claim_status"]).

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss.build import load_all  # noqa: E402
from yss.config import Config  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class GenericItemStatusTests(TempSiteCase):
    def test_valid_claim_status_validates(self):
        # design.yaml already exists in docs/, so give this one a distinct id/file to avoid clashing.
        (self.root / "docs" / "gh7-valid.yaml").write_text(
            "kind: design\nid: gh7-valid\ntitle: GH7 valid\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing.}\n"
            "constraints:\n  - {id: k1, text: A constraint., status: live}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["gh7-valid"]["constraints"][0]["status"], "live")

    def test_bogus_claim_status_fails_naming_the_vocabulary(self):
        (self.root / "docs" / "gh7-bogus.yaml").write_text(
            "kind: design\nid: gh7-bogus\ntitle: GH7 bogus\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing.}\n"
            "constraints:\n  - {id: k1, text: A constraint., status: on-fire}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertTrue(
            any(
                "docs/gh7-bogus.yaml" in e and "constraints/0/status" in e
                and all(v in e for v in ("live", "decided", "open", "superseded"))
                for e in loaded.errors
            ),
            loaded.errors,
        )

    def test_plan_milestone_work_status_still_validates(self):
        """The composition test: item_base.status must not intersect with a kind's own x-vocab."""
        (self.root / "docs" / "gh7-plan.yaml").write_text(
            "kind: plan\nid: gh7-plan\ntitle: GH7 plan\n"
            "milestones:\n  - {id: m1, title: M1, status: planned}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["gh7-plan"]["milestones"][0]["status"], "planned")
        # The real, pre-existing plan.yaml (milestones use work_status throughout) must also load clean.
        self.assertIn("plan", loaded.docs)

    def test_item_base_status_has_no_enum(self):
        """A status value from an unrelated vocabulary is accepted by item_base itself (no enum)
        as long as nothing more specific constrains the field - proving item_base.status is open."""
        (self.root / "docs" / "gh7-open.yaml").write_text(
            "kind: codemap\nid: gh7-open\ntitle: GH7 codemap\n"
            "modules:\n  - {id: m1, path: yss/build.py, purpose: x, status: dropped}\n"
            "roots:\n  - {path: yss, purpose: source, status: whatever-a-human-wrote}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])

    def test_collection_overriding_claim_status_is_honoured(self):
        (self.root / "examples" / "demo-musing" / "collection.yaml").write_text(
            "title: Demo\nvocabularies: {risk_status: [open, watching, resolved], "
            "claim_status: [alive, dead]}\n"
            "dynamic: {sources: {notes: {provider: hooks:notes}}}\n",
            encoding="utf-8",
        )
        (self.root / "examples" / "demo-musing" / "docs" / "design.yaml").write_text(
            "kind: design\nid: design\ntitle: Demo design\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing.}\n"
            "constraints:\n  - {id: k1, text: A constraint., status: alive}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["demo-musing/design"]["constraints"][0]["status"], "alive")

        # The default vocabulary's own values ("live") are no longer valid inside this collection.
        (self.root / "examples" / "demo-musing" / "docs" / "design.yaml").write_text(
            "kind: design\nid: design\ntitle: Demo design\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing.}\n"
            "constraints:\n  - {id: k1, text: A constraint., status: live}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        loaded = load_all(cfg)
        self.assertTrue(
            any(
                "demo-musing" in e and "constraints/0/status" in e and "alive" in e and "dead" in e
                for e in loaded.errors
            ),
            loaded.errors,
        )


if __name__ == "__main__":
    unittest.main()

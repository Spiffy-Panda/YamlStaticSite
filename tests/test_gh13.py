"""Regression tests for GH-13: design components use `claim_status`, not `work_status`.

A design component is a claim about the architecture ("this part exists and is current"), not a
unit of work, so it could never be `live` while `components[].status` was pinned to `work_status`
(planned|active|blocked|done|dropped). It now declares `x-vocab: claim_status`
(live|decided|open|superseded) like every other design list - principles, interfaces, flows,
constraints and alternatives already did. `item_base.status` in the envelope stays un-enumerated
(see test_gh07), so this is an explicit per-kind declaration, not a deletion.

Migration: existing design docs with `status: active` on a component must move to `live`.

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

CLAIM_STATUS = ("live", "decided", "open", "superseded")


class ComponentClaimStatusTests(TempSiteCase):
    def test_component_status_live_validates(self):
        (self.root / "docs" / "gh13-live.yaml").write_text(
            "kind: design\nid: gh13-live\ntitle: GH13 live\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing., status: live}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["gh13-live"]["components"][0]["status"], "live")

    def test_component_status_active_fails_naming_claim_status(self):
        """`active` is a work_status value; on a component it must now be rejected."""
        (self.root / "docs" / "gh13-active.yaml").write_text(
            "kind: design\nid: gh13-active\ntitle: GH13 active\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing., status: active}\n",
            encoding="utf-8",
        )
        loaded = load_all(self.cfg)
        self.assertTrue(
            any(
                "docs/gh13-active.yaml" in e
                and "components/0/status" in e
                and all(v in e for v in CLAIM_STATUS)
                for e in loaded.errors
            ),
            loaded.errors,
        )

    def test_pilot_design_doc_loads_clean(self):
        """The repo's own docs/design.yaml is migrated: every component carries a claim_status."""
        loaded = load_all(self.cfg)
        self.assertFalse(
            [e for e in loaded.errors if "design.yaml" in e or "components/" in e], loaded.errors
        )
        components = loaded.docs["design"]["components"]
        self.assertTrue(components)
        for component in components:
            self.assertIn(
                component.get("status"),
                CLAIM_STATUS,
                f"component {component['id']} has status {component.get('status')!r}",
            )

    def test_collection_overriding_claim_status_covers_components(self):
        """A collection's own claim_status vocabulary applies to components too."""
        (self.root / "examples" / "demo-musing" / "collection.yaml").write_text(
            "title: Demo\nvocabularies: {risk_status: [open, watching, resolved], "
            "claim_status: [alive, dead]}\n"
            "dynamic: {sources: {notes: {provider: hooks:notes}}}\n",
            encoding="utf-8",
        )
        (self.root / "examples" / "demo-musing" / "docs" / "design.yaml").write_text(
            "kind: design\nid: design\ntitle: Demo design\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing., status: alive}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        loaded = load_all(cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.docs["demo-musing/design"]["components"][0]["status"], "alive")

        # ...and the default vocabulary's own "live" is not valid inside that collection.
        (self.root / "examples" / "demo-musing" / "docs" / "design.yaml").write_text(
            "kind: design\nid: design\ntitle: Demo design\n"
            "components:\n  - {id: c1, name: C1, responsibility: Does a thing., status: live}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        loaded = load_all(cfg)
        self.assertTrue(
            any(
                "demo-musing" in e
                and "components/0/status" in e
                and "alive" in e
                and "dead" in e
                for e in loaded.errors
            ),
            loaded.errors,
        )


if __name__ == "__main__":
    unittest.main()

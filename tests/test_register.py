"""Tests for the contradiction register and the live-rules generator.

The generator's whole value is two properties, and both are only worth having if they are proved:

**Verbatim, never a second copy** - a rule's text comes from the register row or from the bound item
itself, so the output cannot drift into disagreeing with its source.

**Fail, never drop** - every case below is a refusal, because a rule that vanishes to a missed
pattern is indistinguishable, to the agent reading the output, from a rule that does not exist.
Each test names the failure it pins; together they are the eleven hard errors the instrument ships
with.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.liverules import LiveRulesError, find_spec, generate, is_stale, specs  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


HOME = """kind: generic
id: rules
mnemonic: REG
title: Rules
data:
  items:
    - id: one
      handle: REG.1
      title: The first rule, which binds.
    - id: two
      handle: REG.2
      title: The second rule.
"""

REGISTER = """kind: register
id: register
title: Test register
visibility: private
updated: 2026-09-07
covers: [REG]
entries:
  - {subject: REG.1, state: live, lives_in: docs/rules.yaml}
  - {subject: REG.2, state: live, lives_in: docs/rules.yaml}
"""


class RegisterCase(TempSiteCase):
    """A minimal register over a two-handle series in the site root."""

    def setUp(self):
        super().setUp()
        self.write_doc("rules.yaml", HOME)
        self.write_doc("register.yaml", REGISTER)
        site = self.root / "site.yaml"
        site.write_text(
            site.read_text(encoding="utf-8")
            # `sweep_ext` narrows what the mention sweep reads. Without it the sweep reads this test
            # file itself, where REG.3 and REG.9 appear as fixtures with no rows - the sweep working
            # exactly as intended, and inconvenient here for that reason.
            + "\nregister:\n  doc: register\n  out: live-rules\n  sweep_ext: ['.yaml', '.md']\n  series:\n"
              "    - prefix: REG\n      label: Test rules\n      strike_forms: ['~~{handle}']\n",
            encoding="utf-8")
        self.cfg = Config.load(self.root)

    def run_generate(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [], "the site must load before the register can be projected")
        return generate(self.cfg, loaded.docs, loaded.handles, find_spec(self.cfg, ""))

    def refusal(self) -> list[str]:
        with self.assertRaises(LiveRulesError) as caught:
            self.run_generate()
        return caught.exception.problems

    def assertRefuses(self, needle: str):
        problems = self.refusal()
        self.assertTrue(any(needle in p for p in problems), problems)


class ProjectionTests(RegisterCase):
    def test_config_is_discovered(self):
        found = {(s.collection.id or "<root>"): s.prefixes for s in specs(self.cfg)}
        self.assertEqual(found["<root>"], ["REG"])
        self.assertEqual(found["demo-musing"], ["DMP"], "the shipped worked example is discovered too")

    def test_rule_text_comes_from_the_item(self):
        _, text = self.run_generate()
        self.assertIn("The first rule, which binds.", text,
                      "a row with no `says:` quotes the item the handle names")

    def test_says_wins_when_the_row_states_one(self):
        self.write_doc("register.yaml", REGISTER.replace(
            "{subject: REG.1, state: live, lives_in: docs/rules.yaml}",
            "{subject: REG.1, state: live, lives_in: docs/rules.yaml, says: A faithful restatement.}"))
        _, text = self.run_generate()
        self.assertIn("A faithful restatement.", text)

    def test_deterministic(self):
        first = self.run_generate()[1]
        self.assertEqual(first, self.run_generate()[1], "no clock, so a no-op run is byte-identical")

    def test_ships_its_own_staleness_gate(self):
        _, text = self.run_generate()
        self.assertIn("live-rules", text)
        self.assertIn("--check", text, "the command evidence claim ships with the output (R.8)")

    def test_check_detects_a_stale_output(self):
        path, text = self.run_generate()
        self.assertTrue(is_stale(path, text), "nothing has been written yet")
        path.write_text(text, encoding="utf-8")
        self.assertFalse(is_stale(path, text))
        path.write_text(text + "\n# hand-edited\n", encoding="utf-8")
        self.assertTrue(is_stale(path, text))

    def test_retired_reaches_the_list_as_a_bare_number(self):
        self.write_doc("rules.yaml", HOME.replace("      title: The second rule.",
                                                  "      title: ~~REG.2 The second rule.~~"))
        self.write_doc("register.yaml", REGISTER.replace(
            "{subject: REG.2, state: live", "{subject: REG.2, state: retired"))
        _, text = self.run_generate()
        self.assertNotIn("The second rule", text,
                         "a retired rule's wording never reaches the list a working agent loads")
        self.assertIn("numbers:", text)
        self.assertIn("- 2", text, "the spent number is listed so it reads as spent, not missing")


class FailureTests(RegisterCase):
    """The eleven hard errors, one test each."""

    def test_1_shape_changed(self):
        self.write_doc("register.yaml", "kind: register\nid: register\ntitle: T\nupdated: 2026-09-07\nentries: []\n")
        self.assertRefuses("its shape changed")

    def test_1c_covers_disagrees_with_the_config(self):
        self.write_doc("register.yaml", REGISTER.replace("covers: [REG]", "covers: [REG, ZZZ]"))
        self.assertRefuses("this will not guess")

    def test_1b_missing_updated(self):
        self.write_doc("register.yaml", REGISTER.replace("updated: 2026-09-07\n", ""))
        self.assertRefuses("no `updated:`")

    def test_2_row_missing_a_field(self):
        self.write_doc("register.yaml", REGISTER.replace(
            "{subject: REG.1, state: live, lives_in: docs/rules.yaml}", "{subject: REG.1, lives_in: docs/rules.yaml}"))
        loaded = load_all(self.cfg)
        self.assertTrue(any("state" in e for e in loaded.errors), loaded.errors)

    def test_2b_duplicate_number(self):
        self.write_doc("register.yaml", REGISTER.replace(
            "{subject: REG.2, state: live", "{subject: REG.1, state: live"))
        self.assertRefuses("two rows may not share a number")

    def test_3_state_outside_the_vocabulary(self):
        self.write_doc("register.yaml", REGISTER.replace("state: live, lives_in: docs/rules.yaml}\n  - {subject: REG.2",
                                                         "state: pending, lives_in: docs/rules.yaml}\n  - {subject: REG.2"))
        loaded = load_all(self.cfg)
        self.assertTrue(any("pending" in e for e in loaded.errors), loaded.errors)

    def test_4_empty_says(self):
        self.write_doc("register.yaml", REGISTER.replace(
            "lives_in: docs/rules.yaml}\n  - {subject: REG.2", "lives_in: docs/rules.yaml, says: ''}\n  - {subject: REG.2"))
        self.assertRefuses("a rule with no text is worse than no rule")

    def test_5_lives_in_does_not_exist(self):
        self.write_doc("register.yaml", REGISTER.replace("lives_in: docs/rules.yaml}\n  - {subject: REG.2",
                                                         "lives_in: docs/gone.yaml}\n  - {subject: REG.2"))
        self.assertRefuses("does not exist")

    def test_6_handle_not_mentioned_in_its_home_file(self):
        self.write_doc("other.yaml", "kind: generic\nid: other\ntitle: O\ndata: {}\n")
        self.write_doc("register.yaml", REGISTER.replace("lives_in: docs/rules.yaml}\n  - {subject: REG.2",
                                                         "lives_in: docs/other.yaml}\n  - {subject: REG.2"))
        self.assertRefuses("the register has drifted")

    def test_7_retired_but_not_struck(self):
        self.write_doc("register.yaml", REGISTER.replace("{subject: REG.2, state: live", "{subject: REG.2, state: retired"))
        self.assertRefuses("Record the verdict where the handle lives first")

    def test_7b_struck_but_not_retired(self):
        self.write_doc("rules.yaml", HOME.replace("      title: The second rule.",
                                                  "      title: ~~REG.2 The second rule.~~"))
        self.assertRefuses("it is not this program's place to pick which")

    def test_8b_sub_series_do_not_gap_each_other(self):
        """A tagged series counts its own ordinals; REG.D1 leaves no hole at 1 in REG.Q."""
        self.write_doc("rules.yaml", HOME.replace("handle: REG.1", "handle: REG.Q1").replace(
            "handle: REG.2", "handle: REG.D1"))
        self.write_doc("register.yaml", REGISTER.replace("{subject: REG.1,", "{subject: REG.Q1,").replace(
            "{subject: REG.2,", "{subject: REG.D1,"))
        _, text = self.run_generate()
        self.assertIn("REG.Q1", text)
        self.assertIn("REG.D1", text)

    def test_8_gap_in_the_series(self):
        self.write_doc("rules.yaml", HOME.replace("handle: REG.2", "handle: REG.3"))
        self.write_doc("register.yaml", REGISTER.replace("{subject: REG.2,", "{subject: REG.3,"))
        self.assertRefuses("a gap is a missing row, not a free number")

    def test_9_handle_written_with_no_row(self):
        (self.root / "loose.md").write_text("this names REG.4, which has no row\n", encoding="utf-8")
        self.assertRefuses("no row on the register")

    def test_9b_unminted_excuses_it(self):
        (self.root / "loose2.md").write_text("this names REG.4, promised but unspent\n", encoding="utf-8")
        site = self.root / "site.yaml"
        site.write_text(site.read_text(encoding="utf-8").replace(
            "      strike_forms: ['~~{handle}']\n",
            "      strike_forms: ['~~{handle}']\n      unminted:\n        - {handle: REG.4, why: named by the design before it exists}\n"),
            encoding="utf-8")
        self.cfg = Config.load(self.root)
        self.run_generate()  # no refusal

    def test_10_pair_names_a_handle_with_no_row(self):
        self.write_doc("register.yaml", REGISTER + (
            "pairs:\n  - id: p1\n    kind: hard\n    a: {subject: REG.1, quote: one}\n"
            "    b: {subject: REG.9, quote: nine}\n"))
        loaded = load_all(self.cfg)
        if loaded.errors:  # REG.9 is not a bound handle, so the handle index rejects it first
            self.assertTrue(any("REG.9" in e for e in loaded.errors), loaded.errors)
        else:
            self.assertRefuses("no row on the register")

    def test_10b_closed_pair_with_no_retired_subject(self):
        self.write_doc("register.yaml", REGISTER + (
            "pairs:\n  - id: p1\n    kind: hard\n    state: closed 2026-09-07\n"
            "    a: {subject: REG.1, quote: one}\n    b: {subject: REG.2, quote: two}\n"))
        self.assertRefuses("neither subject is retired")

    def test_11_pair_missing_a_quote(self):
        self.write_doc("register.yaml", REGISTER + (
            "pairs:\n  - id: p1\n    kind: hard\n    a: {subject: REG.1, quote: one}\n"
            "    b: {subject: REG.2, quote: ''}\n"))
        self.assertRefuses("inventing them is worse than missing them")


class AnchorTests(TempSiteCase):
    """A register row anchors on its own id, so evidence links and references resolve (gh-11)."""

    def test_rows_anchor_on_their_item_id(self):
        loaded = load_all(self.cfg)
        from yss.render import Renderer
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        html = renderer.prefab("contradiction-register", {
            "entries": [{"id": "e-one", "subject": "XYZ.1", "state": "live", "says": "a rule"}],
        })
        self.assertIn('id="e-one"', html, "an evidence link resolves to the row's own id")
        self.assertIn('id="h-xyz-1"', html, "and the handle form stays for a reader to paste")

    def test_the_demo_register_leaves_no_dead_reference(self):
        from yss.build import build
        report = build(self.cfg, "private", out_dir=self.root / "out", run_dynamic=False)
        dead = [w for w in report.warnings if w.startswith("dead reference") and "register" in w]
        self.assertEqual(dead, [])


class DemoCollectionTests(TempSiteCase):
    """The shipped worked example: the demo collection's register generates and stays current."""

    def test_demo_register_is_current(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        spec = find_spec(self.cfg, "demo-musing")
        path, text = generate(self.cfg, loaded.docs, loaded.handles, spec)
        self.assertFalse(is_stale(path, text),
                         "the committed live-rules list matches its register; re-run `yss live-rules`")

    def test_register_and_its_page_are_private(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.docs["demo-musing/register"]["visibility"], "private")
        page = next(p for p in loaded.pages if p["id"] == "demo-musing/register")
        self.assertEqual(page["visibility"], "private")


if __name__ == "__main__":
    unittest.main()

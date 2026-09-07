"""Tests for persistent item handles: the index, resolution, citations and the mention sweep.

Handles are a second key on the index yss already has, so the properties worth pinning are the ones
that make a second key safe: one spelling in the index and case-folding lookup, uniqueness enforced
across the whole site rather than per doc, an archived item that still resolves, and a citation that
resolves to nothing in a target where its doc is private - inert text, never a leak and never a
build failure (H.5).

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.handles import (  # noqa: E402
    build_index,
    canon,
    format_handle,
    parse_handle,
    parse_scoped,
    sweep,
    unregistered,
)
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


DOC = """kind: generic
id: {id}
mnemonic: {mnemonic}
title: {title}
data:
  rules:
{rows}
"""


def doc(id_: str, mnemonic: str, rows: list[tuple[str, str, str]], title: str = "T") -> str:
    body = "".join(
        f"    - id: {rid}\n      handle: {handle}\n      title: {text}\n" for rid, handle, text in rows
    )
    return DOC.format(id=id_, mnemonic=mnemonic, title=title, rows=body)


#: A doc participating in two series at once - legal since adr-053, and the shape that made the
#: rule necessary: a musing whose questions and decisions grew into one file.
DOC_TWO_SERIES = """kind: generic
id: h2c
mnemonic: [ONE, TWO]
title: T
data:
  rules:
    - {id: a, handle: ONE.1, title: first}
    - {id: b, handle: TWO.1, title: second}
"""


class ParsingTests(unittest.TestCase):
    def test_parse_handle(self):
        self.assertEqual(parse_handle("PLN.7"), ("PLN", "", 7))
        self.assertEqual(parse_handle("pln.7"), ("PLN", "", 7), "lookup folds case")
        self.assertEqual(parse_handle(" AGP.12 "), ("AGP", "", 12))

    def test_sub_series_tag(self):
        """A tag splits one mnemonic into several series, so a doc of questions and decisions
        does not have to claim two mnemonics."""
        ref = parse_handle("VFB.Q5")
        self.assertEqual(ref, ("VFB", "Q", 5))
        self.assertEqual(ref.series, "VFB.Q")
        self.assertEqual(ref.handle, "VFB.Q5")
        self.assertEqual(parse_handle("vfb.q5").handle, "VFB.Q5", "one spelling in the index")
        self.assertEqual(parse_handle("PLN.7").series, "PLN", "an untagged series is its mnemonic")

    def test_not_handles(self):
        for text in ("PLN", "PLN.", ".7", "PLN.7.1", "PLN.07", "a/b.1", "PLN.x",
                     "OS.Design1", "VFB.QQQQ1"):
            self.assertIsNone(parse_handle(text), text)

    def test_scoped(self):
        self.assertEqual(parse_scoped(".7"), ("", 7))
        self.assertEqual(parse_scoped(".Q5"), ("Q", 5))
        self.assertIsNone(parse_scoped("PLN.7"))

    def test_canonical_form(self):
        self.assertEqual(format_handle("pln", 7), "PLN.7")
        self.assertEqual(format_handle("vfb", 5, "q"), "VFB.Q5")
        self.assertEqual(canon("tsg"), "TSG")


class IndexTests(TempSiteCase):
    def test_repo_indexes_cleanly(self):
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertTrue(len(loaded.handles) > 100, "the pilot's own docs are backfilled")
        self.assertIn("PLN", loaded.handles.mnemonics)

    def test_one_spelling_two_cases(self):
        self.write_doc("h1.yaml", doc("h1", "hx", [("a", "hx.1", "first"), ("b", "HX.2", "second")]))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertIsNotNone(loaded.handles.get("HX.1"))
        self.assertIsNotNone(loaded.handles.get("hx.2"))
        self.assertEqual(loaded.handles.get("hx.2").spelled, "HX.2", "storage keeps what the file wrote")

    def test_two_docs_may_declare_one_series(self):
        """A series belongs to the site (adr-053): a musing whose items grew across two files is
        the normal case, and handle uniqueness is what actually keeps them apart."""
        self.write_doc("h2.yaml", doc("h2", "dup", [("a", "dup.1", "x")]))
        self.write_doc("h3.yaml", doc("h3", "DUP", [("b", "DUP.2", "y")]))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.handles.get("DUP.1").doc_id, "h2")
        self.assertEqual(loaded.handles.get("DUP.2").doc_id, "h3")

    def test_a_shared_series_still_cannot_reuse_a_number(self):
        self.write_doc("h2b.yaml", doc("h2b", "shr", [("a", "shr.1", "x")]))
        self.write_doc("h3b.yaml", doc("h3b", "SHR", [("b", "SHR.1", "y")]))
        loaded = load_all(self.cfg)
        self.assertTrue(any("never reused" in e for e in loaded.errors), loaded.errors)

    def test_a_doc_may_declare_several_mnemonics(self):
        self.write_doc("h2c.yaml", DOC_TWO_SERIES)
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.handles.mnemonic_of("h2c"), "ONE", "the first listed is the scope")
        self.assertIsNotNone(loaded.handles.get("TWO.1"))

    def test_handle_reuse_is_an_error(self):
        self.write_doc("h4.yaml", doc("h4", "rus", [("a", "rus.1", "x"), ("b", "rus.1", "y")]))
        loaded = load_all(self.cfg)
        self.assertTrue(any("never reused" in e for e in loaded.errors), loaded.errors)

    def test_a_handle_may_use_a_series_another_doc_declares(self):
        self.write_doc("h5a.yaml", doc("h5a", "elsewhere", [("z", "elsewhere.9", "declared here")]))
        self.write_doc("h5.yaml", doc("h5", "own", [("a", "elsewhere.1", "used here")]))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [], "the series is the site's, not one doc's (adr-053)")

    def test_a_prefix_no_doc_declares_is_an_error(self):
        """The check that survives adr-053: a typo'd prefix still has nowhere to live."""
        self.write_doc("h6.yaml", "kind: generic\nid: h6\ntitle: T\ndata:\n  rules:\n    - {id: a, handle: NOPE.1, title: x}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("which no doc declares" in e for e in loaded.errors), loaded.errors)

    def test_mint_pointer_and_gaps(self):
        self.write_doc("h7.yaml", doc("h7", "gap", [("a", "gap.1", "x"), ("c", "gap.3", "z")]))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.handles.next_number("gap"), 4)
        self.assertEqual(loaded.handles.gaps("GAP"), [2], "append-only: a gap is a missing row")

    def test_sub_series_count_independently(self):
        """The reason a tag exists: two sub-series of one mnemonic must not gap each other."""
        self.write_doc("h7b.yaml", doc("h7b", "sub", [
            ("q1", "sub.Q1", "first question"), ("q2", "sub.Q2", "second question"),
            ("d1", "sub.D1", "first decision")]))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.assertEqual(loaded.handles.series_keys("SUB"), ["SUB.D", "SUB.Q"])
        self.assertEqual(loaded.handles.gaps("SUB.Q"), [], "Q is 1..2 with nothing missing")
        self.assertEqual(loaded.handles.gaps("SUB.D"), [], "D is 1 with nothing missing")
        self.assertEqual(loaded.handles.next_number("SUB.D"), 2, "each series mints its own next")
        self.assertEqual(loaded.handles.next_number("SUB.Q"), 3)
        self.assertEqual(len(loaded.handles.series("SUB")), 3, "the mnemonic still names them all")

    def test_a_tagged_handle_needs_its_prefix_declared_somewhere(self):
        self.write_doc("h7c.yaml", doc("h7c", "own2", [("a", "nowhere.Q1", "x")]))
        loaded = load_all(self.cfg)
        self.assertTrue(any("which no doc declares" in e for e in loaded.errors), loaded.errors)


class ArchiveTests(TempSiteCase):
    """Archived means not loaded into a working agent's context, not absent from the index."""

    def test_archived_item_still_resolves(self):
        self.write_doc("h8.yaml", doc("h8", "arc", [("a", "arc.1", "live one")]))
        archive = self.root / "docs" / "_archive"
        archive.mkdir(exist_ok=True)
        (archive / "old.yaml").write_text(
            "milestones:\n  - id: gone\n    handle: ARC.2\n    title: the archived one\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        entry = loaded.handles.get("ARC.2")
        self.assertIsNotNone(entry, "an archived handle still resolves")
        self.assertEqual(entry.tier, "archived")
        self.assertEqual(entry.text, "the archived one", "and still quotes exactly")
        self.assertNotIn("h8/gone", loaded.docs, "but the archive is not a doc")

    def test_archived_handle_needs_a_declared_mnemonic(self):
        archive = self.root / "docs" / "_archive"
        archive.mkdir(exist_ok=True)
        (archive / "orphan.yaml").write_text("items:\n  - {id: x, handle: ZZZ.1, title: t}\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        self.assertTrue(any("which no doc declares" in e for e in loaded.errors), loaded.errors)


class ReferenceTests(TempSiteCase):
    def test_handle_reference_validates(self):
        self.write_doc("h9.yaml", doc("h9", "ref", [("a", "ref.1", "the rule")]))
        self.write_doc("h10.yaml", "kind: generic\nid: h10\ntitle: T\ndata: {note: 'see [[REF.1]]'}\n")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])

    def test_unknown_handle_is_an_error(self):
        self.write_doc("h11.yaml", doc("h11", "ref", [("a", "ref.1", "the rule")]))
        self.write_doc("h12.yaml", "kind: generic\nid: h12\ntitle: T\ndata: {note: 'see [[REF.9]]'}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("[[REF.9]]" in e and "no handle" in e for e in loaded.errors), loaded.errors)

    def test_in_scope_form(self):
        self.write_doc("h13.yaml", doc("h13", "sco", [("a", "sco.1", "one"), ("b", "sco.2", "two")]).replace(
            "      title: two\n", "      title: two\n      notes: back to [[.1]]\n"))
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])

    def test_in_scope_form_needs_a_mnemonic_in_scope(self):
        self.write_doc("h14.yaml", "kind: generic\nid: h14\ntitle: T\ndata: {note: 'see [[.1]]'}\n")
        loaded = load_all(self.cfg)
        self.assertTrue(any("in scope" in e for e in loaded.errors), loaded.errors)

    def test_a_real_doc_wins_over_a_handle_shape(self):
        self.write_doc("h15.yaml", doc("h15", "win", [("a", "win.1", "one")]))
        self.write_doc("win.1.yaml", "kind: generic\nid: win.1\ntitle: Doc named like a handle\ndata: {}\n")
        self.write_doc("h16.yaml", "kind: generic\nid: h16\ntitle: T\ndata: {note: 'see [[win.1]]'}\n")
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [], "a doc of that name resolves as a doc")


class CitationTests(TempSiteCase):
    def _renderer(self, target: str = "private") -> Renderer:
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        from yss.visibility import filter_for_target
        docs = {k: v for k, v in ((k, filter_for_target(v, target)) for k, v in loaded.docs.items()) if v}
        pages = [p for p in (filter_for_target(p, target) for p in loaded.pages) if p]
        archived = [(w, filter_for_target(d, target)) for w, d in loaded.archived]
        return Renderer(self.cfg, target, docs, pages, loaded.prefabs, list(loaded.docs), archived=archived)

    def test_citation_carries_the_items_own_words(self):
        self.write_doc("c1.yaml", doc("c1", "cit", [("a", "cit.1", "the exact wording")]))
        html = self._renderer().expand_refs("see [[CIT.1]]")
        self.assertIn('class="yss-cite', html)
        self.assertIn("the exact wording", html, "the tooltip quotes the item, not a paraphrase")
        self.assertIn("CIT.1", html)

    def test_label_overrides_the_shown_text(self):
        self.write_doc("c2.yaml", doc("c2", "lab", [("a", "lab.1", "wording")]))
        html = self._renderer().expand_refs("see [[LAB.1|that rule]]")
        self.assertIn(">that rule<", html)

    def test_private_doc_citation_is_inert_in_public(self):
        self.write_doc("c3.yaml", doc("c3", "prv", [("a", "prv.1", "secret wording")]).replace(
            "kind: generic\n", "kind: generic\nvisibility: private\n", 1))
        html = self._renderer("public").expand_refs("see [[PRV.1]]")
        self.assertNotIn("secret wording", html, "no private wording reaches the public build")
        self.assertNotIn("yss-cite", html, "it renders as plain text")
        self.assertIn("PRV.1", html, "the number itself is allowed (H.5)")

    def test_private_doc_citation_resolves_in_private(self):
        self.write_doc("c4.yaml", doc("c4", "prw", [("a", "prw.1", "secret wording")]).replace(
            "kind: generic\n", "kind: generic\nvisibility: private\n", 1))
        self.assertIn("secret wording", self._renderer("private").expand_refs("see [[PRW.1]]"))

    def test_tooltip_modes(self):
        self.write_doc("c5.yaml", doc("c5", "mod", [("a", "mod.1", "wording here")]))
        renderer = self._renderer()
        renderer.tooltip_lines = renderer.TOOLTIP_MODES["compact"]
        html = renderer._cite(renderer.handles.get("MOD.1"), None)
        self.assertIn("wording here", html)
        self.assertNotIn("docs/c5.yaml", html, "compact hides the source line")

    def test_citation_without_a_page_is_a_span(self):
        self.write_doc("c6.yaml", doc("c6", "nop", [("a", "nop.1", "unpresented")]))
        html = self._renderer().expand_refs("see [[NOP.1]]")
        self.assertIn("<span", html, "no page presents this doc, so there is nothing to link to (H.4)")
        self.assertNotIn("<a ", html)


class SweepTests(TempSiteCase):
    def test_sweep_finds_a_mention_outside_a_doc(self):
        self.write_doc("s1.yaml", doc("s1", "swp", [("a", "swp.1", "one")]))
        (self.root / "note.md").write_text("prose that names SWP.1 and SWP.4\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        found = sweep(self.root, loaded.handles)
        by_handle = {m.handle for m in found}
        self.assertIn("SWP.1", by_handle)
        self.assertIn("SWP.4", by_handle)
        loose = unregistered(found)
        self.assertEqual({m.handle for m in loose}, {"SWP.4"}, "only the one no item carries")

    def test_unminted_excuses_a_mention(self):
        self.write_doc("s2.yaml", doc("s2", "unm", [("a", "unm.1", "one")]))
        (self.root / "note2.md").write_text("names UNM.9, which is promised but unspent\n", encoding="utf-8")
        loaded = load_all(self.cfg)
        found = sweep(self.root, loaded.handles)
        self.assertEqual(unregistered(found, ["unm.9"]), [], "a declared unminted handle is not loose")

    def test_sweep_ignores_prefixes_no_doc_declares(self):
        loaded = load_all(self.cfg)
        (self.root / "note3.md").write_text("version 1.4 and Fig.2 and NOTAPREFIX.3\n", encoding="utf-8")
        found = sweep(self.root, loaded.handles)
        self.assertEqual([m for m in found if m.path == "note3.md"], [],
                         "only a declared prefix reads as a handle")

    def test_sweep_skips_build_output(self):
        loaded = load_all(self.cfg)
        out = self.root / "dist" / "public"
        out.mkdir(parents=True, exist_ok=True)
        (out / "x.html").write_text("PLN.1\n", encoding="utf-8")
        self.assertEqual([m for m in sweep(self.root, loaded.handles) if m.path.startswith("dist/")], [])


if __name__ == "__main__":
    unittest.main()

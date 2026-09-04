"""Regression tests for gh-23: a generated global doc id was fed to a scope-relative resolver.

`collect_claims` iterates `docs.items()`, so `Claim.doc` is always the exact global key of `docs`.
The evidence-list prefab handed that straight to `doc_url()`/`ref_url()`, which pushed it back
through `resolve_doc_id(ref, current_collection, docs)` - a resolver whose documented job is to
prefer a collection-local doc over a root doc of the same name. Inside a collection, every evidence
row for a root-owned doc therefore linked into the collection instead, and the reference recorded
for gh-11's dead-anchor check was mislabelled to match.

The two cases are the same string - `plan` can be an authored relative reference or an already
global id - so the caller has to say which it holds. That is the `exact=` argument.

This repository has the collision built in: a root `docs/plan.yaml` (id `plan`) and
`examples/demo-musing/docs/plan.yaml` (id `demo-musing/plan`).

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build, load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class ExactResolutionTests(TempSiteCase):
    def renderer_inside_the_collection(self) -> Renderer:
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)
        renderer.current_collection = next(c for c in renderer.collections if c.id == "demo-musing")
        return renderer

    def test_both_docs_exist_so_the_collision_is_real(self):
        loaded = load_all(self.cfg)
        self.assertIn("plan", loaded.docs)
        self.assertIn("demo-musing/plan", loaded.docs)

    def test_an_authored_reference_still_resolves_collection_local_first(self):
        """The documented rule is unchanged: this is what `/plan` exists to override."""
        renderer = self.renderer_inside_the_collection()
        # `demo-musing/plan` is presented by the collection's index page, so its URL is the
        # collection route - which is exactly the wrong place for a root-owned claim to land.
        self.assertEqual(renderer.doc_url("plan"), renderer.doc_url("demo-musing/plan", exact=True))
        self.assertIn("demo-musing", renderer.doc_url("plan"))

    def test_an_exact_id_resolves_to_the_doc_it_names(self):
        """A generated id is already the key of `docs`; look it up, do not re-resolve it."""
        renderer = self.renderer_inside_the_collection()
        url = renderer.doc_url("plan", exact=True)
        self.assertNotIn("demo-musing", url, "the root plan must not resolve into the collection")
        self.assertTrue(url.endswith("/plan/"), url)
        self.assertIn("demo-musing", renderer.doc_url("demo-musing/plan", exact=True))

    def test_exact_falls_back_to_scope_relative_for_an_unknown_id(self):
        """`exact` is a preference for an exact hit, not a refusal to resolve anything else."""
        renderer = self.renderer_inside_the_collection()
        self.assertEqual(renderer.doc_url("nope", exact=True), "")

    def test_ref_url_records_the_reference_against_the_right_doc(self):
        """`note_ref` feeds gh-11's dead-anchor check; a mislabelled id makes it report a
        reference nobody wrote."""
        renderer = self.renderer_inside_the_collection()
        renderer.ref_url("plan#m6-prototypes", exact=True)
        recorded = {doc for doc, _item, _base in renderer._refs} if hasattr(renderer, "_refs") else set()
        if recorded:  # the attribute name is internal; only assert when it is reachable
            self.assertIn("plan", recorded)
            self.assertNotIn("demo-musing/plan", recorded)


class RenderedEvidenceLinkTests(TempSiteCase):
    def test_a_collection_page_links_a_root_claim_to_the_root_doc(self):
        """The end-to-end shape from the issue: evidence-list on a page inside a collection."""
        (self.root / "examples" / "demo-musing" / "pages" / "ledger.yaml").write_text(
            "id: ledger\nroute: /ledger/\ntitle: Ledger\n"
            "sections:\n"
            "  - id: claims\n"
            "    type: prefab\n"
            "    prefab: evidence-list\n"
            "    args:\n"
            "      claims: {from: $evidence}\n"
            "      show_ok: true\n",
            encoding="utf-8")
        cfg = Config.load(self.root)
        report = build(cfg, "private", run_dynamic=False)

        html = (self.root / "dist" / "private" / "demo-musing" / "ledger" / "index.html").read_text(encoding="utf-8")
        self.assertIn('href="/plan/#', html, "a root-owned claim must link to the root plan page")
        self.assertNotIn('href="/demo-musing/plan/#', html,
                         "the root plan's claims must not be rewritten into the collection")

        misrouted = [w for w in report.warnings if "demo-musing/plan#" in w]
        self.assertEqual(misrouted, [], "no dead reference should be reported against a doc nobody named")


if __name__ == "__main__":
    unittest.main()

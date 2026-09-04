"""Regression tests for GH-15: the <title> repeated a segment it already had.

`<title>` was a plain concatenation - page title, then the collection title when there was one,
then the site name - so a page whose own title equalled the next segment said it twice: the repo
home page (`title: YamlStaticSite`, the same as `site.name`) rendered `YamlStaticSite ·
YamlStaticSite`, and a collection's landing page (`Demo musing`, the same as the collection title)
rendered `Demo musing · Demo musing · YamlStaticSite`.

The fix lives entirely in `yss/templates/default.html`: the crumbs are collected into a list and a
segment equal to the one already at the end is dropped, then the list is joined with ` · `. The
rule is *adjacent* dedup, not set-dedup, so a page inside a collection whose title happens to equal
the site name keeps all three segments - the middle crumb still carries information and dropping
either end would lie about where the page sits.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss.build import build  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

SEP = " \u00b7 "
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S)


class TitleDedupTests(TempSiteCase):
    def title_of(self, *parts: str, target: str = "private") -> str:
        out = build(self.cfg, target, run_dynamic=False).out_dir
        html = out.joinpath(*parts).read_text(encoding="utf-8")
        found = TITLE_RE.search(html)
        self.assertIsNotNone(found, f"no <title> in {'/'.join(parts)}")
        return found.group(1)

    def write_demo_page(self, name: str, title: str) -> None:
        page = self.root / "examples" / "demo-musing" / "pages" / f"{name}.yaml"
        page.write_text(
            f"id: {name}\ntitle: {title}\n"
            "summary: A sub-page added by the GH-15 regression test.\n"
            "sections:\n"
            f"  - {{id: body, type: markdown, markdown: 'Sub-page {name}.'}}\n",
            encoding="utf-8",
        )

    def test_home_page_title_is_the_site_name_once(self):
        """page.title == site.name and no collection -> one segment, not two."""
        self.assertEqual(self.title_of("index.html"), "YamlStaticSite")

    def test_home_page_title_is_the_site_name_once_in_public(self):
        self.assertEqual(self.title_of("index.html", target="public"), "YamlStaticSite")

    def test_collection_index_drops_the_repeated_collection_title(self):
        """page.title == collection.title -> the collection crumb goes, the site name stays."""
        self.assertEqual(
            self.title_of("demo-musing", "index.html"),
            f"Demo musing{SEP}YamlStaticSite",
        )

    def test_collection_sub_page_keeps_all_three_segments_in_order(self):
        self.write_demo_page("gh15-sub", "Field notes")
        self.assertEqual(
            self.title_of("demo-musing", "gh15-sub", "index.html"),
            f"Field notes{SEP}Demo musing{SEP}YamlStaticSite",
        )

    def test_root_page_with_a_distinct_title_keeps_two_segments(self):
        self.assertEqual(self.title_of("plan", "index.html"), f"Plan{SEP}YamlStaticSite")

    def test_dedup_is_adjacent_only_so_a_site_named_sub_page_keeps_its_collection(self):
        """Title == site.name but != collection.title: nothing is adjacent, nothing is dropped."""
        self.write_demo_page("gh15-samename", "YamlStaticSite")
        self.assertEqual(
            self.title_of("demo-musing", "gh15-samename", "index.html"),
            f"YamlStaticSite{SEP}Demo musing{SEP}YamlStaticSite",
        )

    def test_no_title_ever_repeats_a_segment_next_to_itself(self):
        out = build(self.cfg, "private", run_dynamic=False).out_dir
        offenders = []
        for html in out.rglob("index.html"):
            found = TITLE_RE.search(html.read_text(encoding="utf-8"))
            if not found:
                continue
            crumbs = found.group(1).split(SEP)
            if any(a == b for a, b in zip(crumbs, crumbs[1:])):
                offenders.append((html.relative_to(out).as_posix(), found.group(1)))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()

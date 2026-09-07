"""Regression tests for gh-40: `task-list` dropped milestone `notes`.

`item_base` in the plan schema puts `notes` on milestones *and* tasks, and the prefab rendered it
only for tasks - so a note written on a milestone validated, exported to JSON, and then silently
vanished from the page. The fix mirrors the task branch exactly (`prose` div, block `md`), one
line below `depends_on`.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.render import Renderer  # noqa: E402

from test_features import TempSiteCase  # noqa: E402

MILESTONE = {
    "id": "m1-repro",
    "title": "Repro",
    "status": "active",
    "summary": "A summary that always rendered.",
    "notes": "The milestone note that gh-40 dropped.",
    "tasks": [{"id": "t1", "title": "A task", "status": "done",
               "notes": "The task note that always rendered."}],
}


class MilestoneNotesTests(TempSiteCase):
    def setUp(self):
        super().setUp()
        loaded = load_all(self.cfg)
        self.assertEqual(loaded.errors, [])
        self.renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)

    def render(self, milestone: dict) -> str:
        return self.renderer.prefab("task-list", milestones=[milestone])

    def test_the_milestone_note_reaches_the_page(self):
        html = self.render(MILESTONE)
        self.assertIn("The milestone note that gh-40 dropped.", html)

    def test_it_uses_the_same_markup_as_the_task_note(self):
        html = self.render(MILESTONE)
        self.assertEqual(html.count('class="task-notes prose"'), 2,
                         "one note div for the milestone, one for its task")

    def test_the_note_is_rendered_as_block_markdown(self):
        html = self.render({**MILESTONE, "notes": "First para.\n\nSecond para."})
        self.assertIn("<p>First para.</p>", html)
        self.assertIn("<p>Second para.</p>", html)

    def test_the_fields_that_already_worked_still_work(self):
        html = self.render(MILESTONE)
        self.assertIn("A summary that always rendered.", html)
        self.assertIn("The task note that always rendered.", html)

    def test_a_milestone_without_notes_emits_nothing_extra(self):
        html = self.render({k: v for k, v in MILESTONE.items() if k != "notes"})
        self.assertEqual(html.count('class="task-notes prose"'), 1,
                         "only the task's note")


class ParamDocsTests(unittest.TestCase):
    def test_notes_is_documented_on_the_milestone_item(self):
        """The param docs are what a page author reads before binding; an undocumented field
        that silently works is only half a fix."""
        text = (REPO / "yss" / "prefabs" / "task-list.yaml").read_text(encoding="utf-8")
        item_line = next(l for l in text.splitlines() if l.strip().startswith("milestones:"))
        self.assertIn("notes: optional", item_line)


if __name__ == "__main__":
    unittest.main()

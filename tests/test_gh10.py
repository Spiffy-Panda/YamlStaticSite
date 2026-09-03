"""Regression tests for GH-10: card `tone`, `kicker`, and loud failure on an unknown tone.

Run with: python -m unittest discover -s tests -p "test_gh10.py" -v
"""
from __future__ import annotations

import os
import shutil
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import load_all  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.render import Renderer, RenderError  # noqa: E402


def temp_site() -> Path:
    tmp = Path(tempfile.mkdtemp(prefix="yss-gh10-"))
    for name in ("site.yaml", "README.md"):
        if (REPO / name).exists():
            shutil.copy(REPO / name, tmp / name)
    for sub in ("docs", "site", "schemas", "examples"):
        if (REPO / sub).is_dir():
            shutil.copytree(REPO / sub, tmp / sub)
    shutil.copytree(REPO / "tests", tmp / "tests", ignore=shutil.ignore_patterns("__pycache__"))
    shutil.copytree(REPO / "yss", tmp / "yss", ignore=shutil.ignore_patterns("__pycache__"))
    (tmp / "dist").mkdir()
    return tmp


class CardToneKickerTests(unittest.TestCase):
    def setUp(self):
        self.root = temp_site()
        self.cfg = Config.load(self.root)
        self._env = dict(os.environ)
        os.environ.pop("YSS_FORBIDDEN_STRINGS", None)
        os.environ.pop("YSS_FLAG_STRINGS", None)
        loaded = load_all(self.cfg)
        self.renderer = Renderer(self.cfg, "private", loaded.docs, loaded.pages, loaded.prefabs)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.root, ignore_errors=True)

    def test_tone_without_badge_is_toned_and_shows_no_badge(self):
        html = str(self.renderer.prefab("card", {"title": "Frozen exhibit", "tone": "muted"}))
        self.assertIn('class="card tone-muted"', html)
        self.assertNotIn("badge", html)

    def test_badge_without_tone_is_unchanged(self):
        html = str(self.renderer.prefab("card", {"title": "Build pipeline", "badge": "active"}))
        self.assertIn('class="card tone-active"', html)
        self.assertIn(">active<", html)

    def test_kicker_renders_above_the_title(self):
        html = str(self.renderer.prefab("card", {"title": "Live prototype", "kicker": "current · what the build is"}))
        self.assertIn('<div class="card-kicker">current · what the build is</div>', html)
        self.assertLess(html.index("card-kicker"), html.index("card-title"))

    def test_unknown_tone_fails_loudly(self):
        with self.assertRaises(RenderError) as cm:
            self.renderer.prefab("card", {"title": "Live prototype", "tone": "live"})
        message = str(cm.exception)
        self.assertIn("card", message)
        self.assertIn("live", message)


if __name__ == "__main__":
    unittest.main()

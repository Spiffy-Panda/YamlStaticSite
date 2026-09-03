"""Regression tests for gh-6: `yss scan` gitignore awareness and ignore escape hatches.

Every test scans a temporary copy of the pilot site, never the real repo, and a fresh git repo is
initialised in it so gitignore behaviour is exercised for real (not simulated).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.build import build  # noqa: E402
from yss.cli import main  # noqa: E402
from yss.config import Config  # noqa: E402
from yss.visibility import scan_tree  # noqa: E402


def temp_site() -> Path:
    """Copy the pilot site's sources (not dist, not .yss) into a fresh temp directory."""
    tmp = Path(tempfile.mkdtemp(prefix="yss-gh06-"))
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


def git_init(root: Path) -> None:
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)


class TempSiteCase(unittest.TestCase):
    def setUp(self):
        self.root = temp_site()
        self._env = dict(os.environ)
        os.environ.pop("YSS_FORBIDDEN_STRINGS", None)
        os.environ.pop("YSS_FLAG_STRINGS", None)

    def tearDown(self):
        os.environ.clear()
        os.environ.update(self._env)
        shutil.rmtree(self.root, ignore_errors=True)


class ScanGitignoreTests(TempSiteCase):
    # Built by concatenation (not as one literal) so the secret does not itself appear as a
    # contiguous substring in this file - this file gets copied into the scanned tree by
    # temp_site(), and a literal occurrence here would be a spurious self-hit.
    SECRET = "gh6" + "needlestring" + "9x2"

    def plant(self, relpath: str) -> Path:
        """Write a text file under self.root containing the forbidden string, creating dirs."""
        path = self.root / relpath
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"leaked: {self.SECRET} was here\n", encoding="utf-8")
        return path

    def run_scan(self, extra_args: list[str] | None = None) -> tuple[int, str]:
        import io
        import contextlib

        buf = io.StringIO()
        with contextlib.redirect_stdout(buf):
            code = main(["--root", str(self.root), "scan"] + (extra_args or []))
        return code, buf.getvalue()

    def test_gitignored_scratch_is_skipped_and_reported(self):
        git_init(self.root)
        (self.root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        self.plant("scratch/build/output.txt")
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET

        code, out = self.run_scan()
        self.assertEqual(code, 0, out)
        self.assertNotIn("FORBIDDEN", out)
        self.assertIn("0 forbidden", out)
        self.assertIn("1 files (gitignored)", out)

    def test_tracked_file_with_same_string_is_still_found(self):
        git_init(self.root)
        (self.root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        self.plant("scratch/build/output.txt")
        self.plant("docs/leaky.yaml")
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET

        code, out = self.run_scan()
        self.assertEqual(code, 1, out)
        self.assertIn("FORBIDDEN docs/leaky.yaml", out)
        self.assertIn("1 forbidden", out)  # the gitignored copy does not also count
        self.assertIn("1 files (gitignored)", out)

    def test_no_gitignore_flag_finds_it_again(self):
        git_init(self.root)
        (self.root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        self.plant("scratch/build/output.txt")
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET

        code, out = self.run_scan(["--no-gitignore"])
        self.assertEqual(code, 1, out)
        self.assertIn("FORBIDDEN scratch/build/output.txt", out)
        self.assertNotIn("(gitignored)", out)  # gitignore matching is off; only the default-dir floor applies

    def test_ignore_glob_skips_a_path_git_does_not(self):
        # No .gitignore at all here (still a repo, so gitignore logic runs and is a no-op).
        git_init(self.root)
        self.plant("notes/scratchpad.txt")
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET

        code, out = self.run_scan(["--ignore", "notes/*"])
        self.assertEqual(code, 0, out)
        self.assertNotIn("FORBIDDEN", out)
        self.assertIn("1 files (--ignore)", out)

    def test_no_git_directory_is_a_no_op(self):
        # No git init at all: gitignore matching must not error, and must not hide anything.
        (self.root / ".gitignore").write_text("scratch/\n", encoding="utf-8")
        self.plant("scratch/build/output.txt")
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET

        code, out = self.run_scan()
        self.assertEqual(code, 1, out)
        self.assertIn("FORBIDDEN scratch/build/output.txt", out)

    def test_build_output_scan_still_sees_everything(self):
        """The build's own scan_tree(out, forbidden, flags, skip_dirs=()) call must be unaffected:
        it never sets respect_gitignore, so a planted string in a would-be-ignored path is still
        found even if the *output* directory happens to sit inside a repo with a matching .gitignore.
        """
        git_init(self.root)
        (self.root / ".gitignore").write_text("dist/\n", encoding="utf-8")  # output dirs are commonly gitignored
        os.environ["YSS_FORBIDDEN_STRINGS"] = self.SECRET
        (self.root / "docs" / "leaky.yaml").write_text(
            f"kind: generic\ntitle: Leaky\nsummary: contains {self.SECRET} here\ndata: {{}}\n",
            encoding="utf-8",
        )
        cfg = Config.load(self.root)
        from yss.build import BuildError

        with self.assertRaises(BuildError) as cm:
            build(cfg, "public", run_dynamic=False)
        self.assertIn("forbidden string", str(cm.exception))

    def test_build_output_call_site_unaffected_directly(self):
        """Exercise yss/build.py's own scan_tree(...) call shape directly: skip_dirs=() and no
        gitignore/ignore-glob kwargs, against an output-like tree sitting inside a gitignoring repo.
        """
        git_init(self.root)
        (self.root / ".gitignore").write_text("out/\n", encoding="utf-8")
        out_dir = self.root / "out"
        self.plant("out/index.html")
        fhits, whits = scan_tree(out_dir, [self.SECRET], [], skip_dirs=())
        self.assertEqual(len(fhits), 1)
        self.assertEqual(fhits[0][0], "index.html")


if __name__ == "__main__":
    unittest.main()

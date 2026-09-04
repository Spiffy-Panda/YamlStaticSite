"""Regression tests for gh-19: two builders on one `dist/` raced silently.

`yss build --target public` printed its success line and left no `dist/public` on disk, because
`BuildReport.summary()` is composed from in-memory counts and never stats the filesystem: a second
builder's `_safe_clear` had deleted the directory underneath the first one.

Two halves are covered here - the gate (`cmd_build` and `server.rebuild` insist the build's own
`build.json` survived) and the advisory lock (`dist/.<target>.build-lock`, a sibling of the output
so `_safe_clear` never removes it and the Pages artefact never carries it).

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import io
import json
import os
import sys
import time
import unittest
from contextlib import redirect_stdout
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss import cli  # noqa: E402
from yss.build import LOCK_STALE_SECONDS, BuildError, BuildReport, _lock_path, build  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


class MissingOutputGateTests(TempSiteCase):
    def test_cmd_build_fails_when_build_json_is_absent(self):
        """A build that reports success but left nothing on disk is an error, not a success."""
        empty = self.root / "dist" / "private"
        empty.mkdir(parents=True, exist_ok=True)
        report = BuildReport(target="private", out_dir=empty, out_label="dist/private")

        buf = io.StringIO()
        with mock.patch.object(cli, "build", return_value=report), redirect_stdout(buf):
            rc = cli.main(["--root", str(self.root), "build", "--target", "private", "--no-dynamic"])
        out = buf.getvalue()
        self.assertEqual(rc, 1, out)
        self.assertIn("build reported success but dist/private/build.json is missing", out)
        self.assertIn("a watching yss serve?", out)

    def test_real_build_succeeds_and_leaves_no_lock(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            rc = cli.main(["--root", str(self.root), "build", "--target", "private", "--no-dynamic"])
        self.assertEqual(rc, 0, buf.getvalue())
        self.assertTrue((self.root / "dist" / "private" / "build.json").is_file())
        self.assertEqual(sorted(p.name for p in (self.root / "dist").glob("*.build-lock")), [])
        self.assertEqual(sorted(p.name for p in (self.root / "dist").glob(".*build-lock")), [])


class BuildLockTests(TempSiteCase):
    def lock_for(self, target: str = "private") -> Path:
        return _lock_path(self.cfg.out_dir(target).resolve())

    def test_lock_path_sits_outside_the_output_directory(self):
        out = self.cfg.out_dir("public").resolve()
        lock = _lock_path(out)
        self.assertEqual(lock.name, ".public.build-lock")
        self.assertEqual(lock.parent, out.parent)
        self.assertNotIn(out, lock.parents)

    def test_fresh_lock_refuses_the_build(self):
        lock = self.lock_for()
        lock.parent.mkdir(parents=True, exist_ok=True)
        fresh = datetime.now(timezone.utc).isoformat(timespec="seconds")
        lock.write_text(json.dumps({"pid": 4242, "target": "private", "started_at": fresh}), encoding="utf-8")
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", run_dynamic=False)
        message = str(ctx.exception)
        self.assertIn("another build owns", message)
        self.assertIn(str(lock), message)
        self.assertIn("4242", message)
        self.assertTrue(lock.is_file(), "a refused build must not steal the other builder's lock")

    def test_stale_lock_is_replaced_with_a_warning(self):
        lock = self.lock_for()
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(json.dumps({"pid": 4242, "target": "private", "started_at": "2026-01-01T00:00:00+00:00"}), encoding="utf-8")
        old = time.time() - (LOCK_STALE_SECONDS + 60)
        os.utime(lock, (old, old))
        report = build(self.cfg, "private", run_dynamic=False)
        self.assertTrue(any("stale build lock" in w for w in report.warnings), report.warnings)
        self.assertFalse(lock.exists())

    def test_lock_is_released_when_the_build_fails_on_a_forbidden_string(self):
        os.environ["YSS_FORBIDDEN_STRINGS"] = "Structured docs"
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "public", run_dynamic=False)
        self.assertIn("forbidden strings", str(ctx.exception))
        self.assertFalse(self.lock_for("public").exists(), "the finally must cover the raise paths")

    def test_lock_is_released_after_a_successful_build(self):
        build(self.cfg, "private", run_dynamic=False)
        self.assertFalse(self.lock_for().exists())


class GitignoreTests(unittest.TestCase):
    def test_dist_is_gitignored_so_a_stray_lock_cannot_be_committed(self):
        lines = (REPO / ".gitignore").read_text(encoding="utf-8").splitlines()
        self.assertIn("dist/", [line.strip() for line in lines])


if __name__ == "__main__":
    unittest.main()

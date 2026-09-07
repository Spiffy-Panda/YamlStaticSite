"""Regression tests for gh-34: the build lock had no way to wait, and no way to be wrong.

gh-19 gave `dist/<target>/` an advisory lock, which stopped two builders clearing each other's
output. What it could not do was tell a *held* lock from an *abandoned* one: a build stopped with
ctrl-c leaves its file behind, and every build for the next ten minutes was refused on behalf of a
process that no longer exists. The original declined to liveness-check on purpose, because the
obvious `os.kill(pid, 0)` terminates the process on Windows - so `_pid_alive` asks the kernel
directly there, and treats "cannot tell" as alive.

The other half is `--wait`: a watching `yss serve` mid-rebuild is a collision that resolves itself
in a second or two, and pausing for it beats failing on it.

Run with: python -m unittest tests.test_gh34 -v
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
# Also importable as `tests.test_gh__` (not only via `unittest discover -s tests`), so the
# sibling helper import below resolves either way.
sys.path.insert(0, str(Path(__file__).resolve().parent))

from yss import cli  # noqa: E402
from yss.build import BuildError, _acquire_lock, _lock_path, _pid_alive, _release_lock, build  # noqa: E402

from test_features import TempSiteCase  # noqa: E402


_REAPED: list = []


def dead_pid() -> int:
    """A pid that certainly is not running: one we started, waited for, and reaped.

    Not an invented number - those get recycled, and a test that depends on 4242 being free is a
    test that fails on a busy machine.
    """
    proc = subprocess.Popen([sys.executable, "-c", "pass"])
    proc.wait(timeout=60)
    _REAPED.append(proc)  # keep the Popen (and on Windows its handle) so the pid is not recycled
    return proc.pid


class PidLivenessTests(unittest.TestCase):
    def test_our_own_pid_is_alive(self):
        self.assertIs(_pid_alive(os.getpid()), True)

    def test_a_reaped_child_is_not_alive(self):
        self.assertIs(_pid_alive(dead_pid()), False)

    def test_nonsense_pids_are_unknown_rather_than_dead(self):
        """Unknown must never read as dead: the fallback is the old refuse-the-build behaviour."""
        for value in (None, "", "abc", 0, -1):
            self.assertIsNone(_pid_alive(value), value)


class StaleLockReclaimTests(TempSiteCase):
    def lock_for(self, target: str = "private") -> Path:
        return _lock_path(self.cfg.out_dir(target).resolve())

    def write_lock(self, pid: int, target: str = "private") -> Path:
        lock = self.lock_for(target)
        lock.parent.mkdir(parents=True, exist_ok=True)
        lock.write_text(
            json.dumps(
                {
                    "pid": pid,
                    "target": target,
                    "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                }
            ),
            encoding="utf-8",
        )
        return lock

    def test_a_fresh_lock_held_by_a_dead_pid_is_reclaimed_with_a_warning(self):
        gone = dead_pid()
        lock = self.write_lock(gone)
        report = build(self.cfg, "private", run_dynamic=False)
        self.assertTrue(
            any("reclaimed build lock" in w and str(gone) in w for w in report.warnings),
            report.warnings,
        )
        self.assertFalse(lock.exists(), "the build takes and then releases the reclaimed lock")

    def test_a_fresh_lock_held_by_a_live_pid_is_still_obeyed(self):
        """The reclaim must not become a way to steal a lock that is genuinely in use."""
        lock = self.write_lock(os.getpid())
        with self.assertRaises(BuildError) as ctx:
            build(self.cfg, "private", run_dynamic=False)
        self.assertIn("another build owns", str(ctx.exception))
        self.assertTrue(lock.is_file())

    def test_the_refusal_names_both_pids(self):
        alive = os.getpid()
        self.write_lock(alive)
        with self.assertRaises(BuildError) as ctx:
            _acquire_lock(self.cfg.out_dir("private").resolve(), "private")
        message = str(ctx.exception)
        self.assertIn(f"held by pid {alive}", message)
        self.assertIn(f"this process is {os.getpid()}", message)


class WaitTests(TempSiteCase):
    def lock_for(self, target: str = "private") -> Path:
        return _lock_path(self.cfg.out_dir(target).resolve())

    def test_wait_succeeds_when_the_other_builder_lets_go(self):
        out = self.cfg.out_dir("private").resolve()
        held, _ = _acquire_lock(out, "private")
        released = threading.Event()

        def let_go():
            time.sleep(1.0)
            _release_lock(held)
            released.set()

        t = threading.Thread(target=let_go, daemon=True)
        t.start()
        try:
            start = time.monotonic()
            lock, warnings = _acquire_lock(out, "private", wait=30.0)
            elapsed = time.monotonic() - start
        finally:
            t.join(timeout=10)
        self.assertTrue(released.is_set())
        self.assertGreaterEqual(elapsed, 0.5, "it should actually have waited, not raced through")
        self.assertEqual(warnings, [], "a lock released normally is not a reclaim or a stale lock")
        _release_lock(lock)

    def test_wait_gives_up_and_says_who_holds_it(self):
        out = self.cfg.out_dir("private").resolve()
        held, _ = _acquire_lock(out, "private")
        try:
            start = time.monotonic()
            with self.assertRaises(BuildError) as ctx:
                _acquire_lock(out, "private", wait=1.0)
            elapsed = time.monotonic() - start
        finally:
            _release_lock(held)
        self.assertGreaterEqual(elapsed, 1.0)
        self.assertLess(elapsed, 20.0)
        message = str(ctx.exception)
        self.assertIn("another build owns", message)
        self.assertIn(f"held by pid {os.getpid()}", message)
        self.assertIn(f"this process is {os.getpid()}", message)


class WaitFlagTests(unittest.TestCase):
    def parse(self, argv: list[str]):
        return cli.build_parser().parse_args(argv)

    def test_default_is_no_wait(self):
        self.assertEqual(self.parse(["build"]).wait, 0.0)

    def test_bare_flag_means_a_minute(self):
        self.assertEqual(self.parse(["build", "--wait"]).wait, 60.0)

    def test_a_value_is_taken_as_seconds(self):
        self.assertEqual(self.parse(["build", "--wait", "2.5"]).wait, 2.5)

    def test_a_bare_flag_does_not_swallow_the_next_option(self):
        args = self.parse(["build", "--wait", "--target", "public"])
        self.assertEqual(args.wait, 60.0)
        self.assertEqual(args.target, "public")


if __name__ == "__main__":
    unittest.main()

"""Test run provider: runs the unittest suite in a subprocess and reports per-case results as JSON.

Config (site.yaml):
  dynamic:
    sources:
      testruns:
        provider: yss.providers.testruns:collect
        tests_dir: tests          # optional, default "tests"
        pattern: "test*.py"       # optional
        targets: [private]

Also runnable directly:  python -m yss.providers.testruns [tests_dir] [pattern]
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import unittest


def _run(tests_dir: str, pattern: str) -> dict:
    loader = unittest.TestLoader()
    suite = loader.discover(tests_dir, pattern=pattern)
    cases: list[dict] = []

    class Collector(unittest.TestResult):
        def _record(self, test, status, message=None):
            cases.append({"name": test.id(), "status": status, "message": message, "time": round(time.time() - self._t0, 3)})

        def startTest(self, test):
            self._t0 = time.time()
            super().startTest(test)

        def addSuccess(self, test):
            super().addSuccess(test)
            self._record(test, "passed")

        def addFailure(self, test, err):
            super().addFailure(test, err)
            self._record(test, "failed", self._exc_info_to_string(err, test)[-2000:])

        def addError(self, test, err):
            super().addError(test, err)
            self._record(test, "error", self._exc_info_to_string(err, test)[-2000:])

        def addSkip(self, test, reason):
            super().addSkip(test, reason)
            self._record(test, "skipped", reason)

    started = time.time()
    result = Collector()
    suite.run(result)
    duration = round(time.time() - started, 3)
    summary = {
        "total": result.testsRun,
        "passed": sum(1 for c in cases if c["status"] == "passed"),
        "failed": len(result.failures),
        "errors": len(result.errors),
        "skipped": len(result.skipped),
        "duration": duration,
        "ok": result.wasSuccessful(),
    }
    return {"summary": summary, "cases": cases, "tests_dir": tests_dir, "pattern": pattern}


def collect(cfg, spec):
    tests_dir = spec.get("tests_dir", "tests")
    pattern = spec.get("pattern", "test*.py")
    if not (cfg.root / tests_dir).is_dir():
        return {"summary": {"total": 0, "ok": True, "note": f"no {tests_dir}/ directory"}, "cases": []}
    proc = subprocess.run(
        [sys.executable, "-m", "yss.providers.testruns", tests_dir, pattern],
        cwd=cfg.root,
        capture_output=True,
        text=True,
        timeout=spec.get("timeout", 600),
    )
    if proc.returncode != 0 and not proc.stdout.strip():
        raise RuntimeError(f"test runner failed: {proc.stderr.strip()[-800:]}")
    return json.loads(proc.stdout)


if __name__ == "__main__":
    args = sys.argv[1:]
    real_stdout = sys.stdout
    sys.stdout = sys.stderr  # anything the tests print must not pollute the JSON
    try:
        payload = _run(args[0] if args else "tests", args[1] if len(args) > 1 else "test*.py")
    finally:
        sys.stdout = real_stdout
    print(json.dumps(payload))

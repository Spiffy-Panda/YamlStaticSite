"""Regression tests for gh-36: a pinned tag and a local editable install disagree.

gh-20 caught the half-bump - `pyproject.toml` moved without `yss/__init__.py`, or either moved
without a changelog line. It cannot see the other half of the same skew: `yss/` quietly moving on
past the newest tag while the declared version stays put. That is the state the report describes -
`pip install yss@v0.4.0` and a `-e .` checkout of `main` answering to the same version string while
behaving differently.

The release itself is a git operation and belongs to a human, so this file does not assert that a
released changelog entry implies a matching tag. It asserts the weak form only: **if `yss/` has
changed since the newest tag, the declared version is no longer that tag's version.** The two trees
are then at least distinguishable by the string they report, which is the whole of what gh-36 asks
for. Cutting the tag stays a judgement call; being unable to tell the trees apart does not.

Everything here is git-dependent, so it skips - loudly and with the reason named - when git is
missing, when the checkout has no tags, or when it is shallow. CI checks out shallow by default and
a false red there would train people to ignore the file.

This is the `enforced_by: test` behind the `cv-release-drift` convention in the code map.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import subprocess
import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yss  # noqa: E402

TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")


def _git(*args: str) -> subprocess.CompletedProcess:
    """Run git inside the repo, never raising; the caller reads returncode."""
    return subprocess.run(
        ["git", *args],
        cwd=str(REPO),
        capture_output=True,
        text=True,
        timeout=30,
    )


class ReleaseDriftTests(unittest.TestCase):
    def _newest_tag(self) -> str:
        """Return the newest reachable tag, or skip with the condition that tripped."""
        if not (REPO / ".git").exists():
            self.skipTest("not a git checkout (no .git in the repo root) - nothing to compare against")
        try:
            probe = _git("rev-parse", "--is-shallow-repository")
        except (OSError, subprocess.SubprocessError) as exc:
            self.skipTest(f"git is unavailable ({exc.__class__.__name__}) - cannot read tags")
        if probe.returncode != 0:
            self.skipTest("git could not read this checkout (`git rev-parse` failed) - cannot read tags")
        if probe.stdout.strip() == "true":
            self.skipTest("shallow checkout - tags and history are truncated, so drift cannot be judged")

        described = _git("describe", "--tags", "--abbrev=0")
        if described.returncode != 0:
            self.skipTest("no tags reachable from HEAD - there is no release to have drifted from")
        tag = described.stdout.strip()
        if not tag:
            self.skipTest("`git describe --tags --abbrev=0` returned nothing - no tag to compare against")
        return tag

    def test_a_changed_package_no_longer_claims_the_newest_tags_version(self):
        """If yss/ moved past the newest tag, the declared version must not still be the tag's."""
        tag = self._newest_tag()
        match = TAG_RE.match(tag)
        if not match:
            self.skipTest(f"newest tag {tag!r} is not v<major>.<minor>.<patch> - cannot compare it to a version")
        tag_version = ".".join(match.groups())

        diff = _git("diff", "--quiet", tag, "HEAD", "--", "yss/")
        if diff.returncode not in (0, 1):
            self.skipTest(
                f"`git diff {tag} HEAD -- yss/` failed (exit {diff.returncode}) - the tag's tree is "
                f"not available here, which a shallow or partial clone would explain"
            )
        drifted = diff.returncode == 1
        if not drifted:
            self.skipTest(f"yss/ is unchanged since {tag} - the pinned tag and this checkout agree")

        self.assertNotEqual(
            yss.__version__,
            tag_version,
            f"yss/ has changed since {tag} but the package still declares {tag_version} - "
            f"`pip install yss@{tag}` and a local editable checkout report the same version for "
            f"different trees, which is exactly the skew gh-36 reported. Bump the version.",
        )


if __name__ == "__main__":
    unittest.main()

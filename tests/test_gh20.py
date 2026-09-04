"""Regression tests for gh-20: no release since v0.2.0, and no signal that anything had changed.

Nineteen issues' fixes landed on `main` after the `v0.2.0` tag while both version declarations
still read `0.2.0`, so a consumer pinning the tag and a consumer running from `main` reported the
identical string for materially different trees. Nothing surfaced the skew - not a tag, not a
version, not an error.

The release itself is a git operation and cannot be asserted here. What can be asserted is the
half that made the skew invisible and is cheap to catch: the two declarations agreeing with each
other and with the newest released changelog entry. A half-bump - `pyproject.toml` moved and
`yss/__init__.py` forgotten, or either moved without a changelog line - fails here.

This is the `enforced_by: test` behind the `cv-release` convention in the code map.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import re
import sys
import unittest
from pathlib import Path

import yaml

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import yss  # noqa: E402


def _pyproject_version() -> str:
    text = (REPO / "pyproject.toml").read_text(encoding="utf-8")
    match = re.search(r'(?m)^version\s*=\s*"([^"]+)"', text)
    assert match, "pyproject.toml declares no version"
    return match.group(1)


def _releases() -> list[dict]:
    data = yaml.safe_load((REPO / "docs" / "changelog.yaml").read_text(encoding="utf-8"))
    return data["releases"]


class VersionAgreementTests(unittest.TestCase):
    def test_declarations_agree(self):
        """The two places yss declares its version say the same thing."""
        self.assertEqual(
            _pyproject_version(),
            yss.__version__,
            "pyproject.toml and yss/__init__.py disagree - a half-bump is exactly the skew gh-20 reported",
        )

    def test_changelog_has_the_current_version_released(self):
        """The declared version exists in the changelog and is not still `unreleased`."""
        releases = _releases()
        current = yss.__version__
        entry = next((r for r in releases if str(r["version"]) == current), None)
        self.assertIsNotNone(
            entry,
            f"version {current} has no changelog entry; the release notes are the only thing "
            f"that tells a consumer what moved",
        )
        self.assertEqual(
            entry["status"],
            "released",
            f"version {current} is declared in code but its changelog entry is still "
            f"'{entry['status']}' - bump and release together, or neither",
        )

    def test_the_declared_version_is_the_newest_entry(self):
        """Newest-first is the changelog's own rule; the code should be at its head."""
        self.assertEqual(
            str(_releases()[0]["version"]),
            yss.__version__,
            "the newest changelog entry is not the version the package declares - either a "
            "release was noted and never bumped, or a bump landed without notes",
        )


if __name__ == "__main__":
    unittest.main()

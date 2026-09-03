"""Regression test for GitHub issue #5.

Several schema `description:` (and similar) values were written as unquoted
scalars inside a YAML flow mapping (`{...}` on one line). Commas in the prose
were then parsed as flow-mapping entry separators, silently truncating the
description and injecting bogus keys (fragments of the sentence, mapped to
`None`) into the loaded schema dict.

This test renders every schema the registry knows (the same set `yss schema`
can print) and fails if any of them contains such a junk key.

Run with: python -m unittest discover -s tests -v
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from yss.config import Config  # noqa: E402
from yss.loader import SchemaRegistry  # noqa: E402


def _looks_like_prose_fragment(key: str) -> bool:
    """Heuristic for a mapping key that is really a truncated sentence fragment,
    not a legitimate schema keyword (property name, JSON Schema keyword, x-*
    annotation, etc.).
    """
    if not isinstance(key, str):
        return False
    return " " in key or key.endswith(".") or key.endswith(")")


def _find_junk_keys(node, path=""):
    """Recursively walk a loaded schema dict and yield (path, key) for every
    mapping entry whose value is None and whose key looks like a prose
    fragment rather than an intentional schema keyword.
    """
    findings = []
    if isinstance(node, dict):
        for key, value in node.items():
            here = f"{path}.{key}" if path else str(key)
            if value is None and _looks_like_prose_fragment(key):
                findings.append(here)
            findings.extend(_find_junk_keys(value, here))
    elif isinstance(node, list):
        for i, item in enumerate(node):
            findings.extend(_find_junk_keys(item, f"{path}[{i}]"))
    return findings


class TestGH05FlowMappingDescriptions(unittest.TestCase):
    """No schema's flow-mapping descriptions should have been comma-split."""

    @classmethod
    def setUpClass(cls):
        cfg = Config.load(REPO)
        cls.registry = SchemaRegistry(cfg.schema_dirs())

    def test_every_schema_free_of_junk_keys(self):
        names = self.registry.names()
        self.assertTrue(names, "expected the registry to have loaded at least one schema")
        for name in names:
            with self.subTest(schema=name):
                schema = self.registry.get(name)
                junk = _find_junk_keys(schema)
                self.assertEqual(
                    junk,
                    [],
                    f"schema '{name}' ({self.registry.sources[name]}) has junk keys "
                    f"from an unquoted flow-mapping scalar: {junk}",
                )

    def test_known_regression_descriptions_are_whole(self):
        # The two examples called out in GH issue #5, plus the ones found by the
        # same sweep, must round-trip as their full intended sentence.
        plan = self.registry.get("doc.plan")
        options_desc = plan["properties"]["open_questions"]["items"]["properties"]["options"]["description"]
        self.assertEqual(options_desc, "Candidate answers, so a verdict can pick one.")

        owner_desc = plan["$defs"]["task"]["properties"]["owner"]["description"]
        self.assertEqual(owner_desc, "Handle or role (agent, human, ci).")


if __name__ == "__main__":
    unittest.main()

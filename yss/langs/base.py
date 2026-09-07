"""What every language indexer shares: the range type and the one error it may raise.

Its own module so a language can import it without importing the registry that imports it.
"""
from __future__ import annotations

#: (start, end) - the inclusive 1-based line span of one name within one file.
Range = tuple[int, int]


class SymbolError(Exception):
    """A file could not be read or parsed. Carries a repo-relative path only, never absolute.

    The message reaches dynamic envelopes and the public build scans its own output for the
    checkout path, so an absolute path in here would fail the deploy (adr-004).
    """

    def __init__(self, rel: str, reason: str) -> None:
        super().__init__(f"{rel}: {reason}")
        self.rel = rel
        self.reason = reason

"""Build information provider: counts and versions, never absolute paths."""
from __future__ import annotations

import platform
import re
import subprocess
from datetime import datetime, timezone
from pathlib import Path

from .. import __version__

# `git describe --tags --always --dirty` yields a tag, a tag-distance-hash, or a bare hash,
# optionally suffixed `-dirty`. Anything outside that shape is refused rather than published:
# this value reaches dist/, and a path must never ride along inside it.
_REF_RE = re.compile(r"^[A-Za-z0-9._][A-Za-z0-9._+\-]{0,63}$")


def _git_ref() -> str | None:
    """Describe the yss package's own checkout, or None if that is not knowable.

    Never raises and never returns anything path-shaped: git missing, no repo, a failed or
    slow subprocess, and unexpected output all collapse to None.
    """
    root = Path(__file__).resolve().parents[2]
    if not (root / ".git").exists():
        return None
    try:
        proc = subprocess.run(
            ["git", "describe", "--tags", "--always", "--dirty"],
            cwd=str(root),
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if proc.returncode != 0:
        return None
    ref = proc.stdout.strip()
    # stderr is deliberately dropped - git error text can quote the repository path.
    if not _REF_RE.match(ref):
        return None
    return ref


def collect(cfg, spec):
    from ..build import load_all

    loaded = load_all(cfg)
    kinds: dict[str, int] = {}
    for doc in loaded.docs.values():
        kinds[doc.get("kind", "?")] = kinds.get(doc.get("kind", "?"), 0) + 1
    return {
        "site": cfg.site.get("name"),
        "yss_version": __version__,
        "yss_ref": _git_ref(),
        "python": platform.python_version(),
        "collected_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "docs": len(loaded.docs),
        "docs_by_kind": kinds,
        "pages": len(loaded.pages),
        "prefabs": len(loaded.prefabs),
        "schemas": len(loaded.registry.schemas),
        "validation_errors": len(loaded.errors),
        "targets": list(cfg.targets),
    }

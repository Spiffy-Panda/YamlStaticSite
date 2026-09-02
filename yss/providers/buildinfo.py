"""Build information provider: counts and versions, never absolute paths."""
from __future__ import annotations

import platform
from datetime import datetime, timezone

from .. import __version__


def collect(cfg, spec):
    from ..build import load_all

    loaded = load_all(cfg)
    kinds: dict[str, int] = {}
    for doc in loaded.docs.values():
        kinds[doc.get("kind", "?")] = kinds.get(doc.get("kind", "?"), 0) + 1
    return {
        "site": cfg.site.get("name"),
        "yss_version": __version__,
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

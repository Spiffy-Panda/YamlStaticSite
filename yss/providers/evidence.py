"""Evidence provider: run `yss check` (optionally with commands) and expose the claims as runtime data.

site.yaml:
  dynamic:
    sources:
      evidence: {provider: yss.providers.evidence:collect, run_commands: true, targets: [private]}
"""
from __future__ import annotations


def collect(cfg, spec):
    from ..build import load_all
    from ..evidence import check

    loaded = load_all(cfg)
    if loaded.errors:
        return {"errors": loaded.errors, "claims": [], "summary": {}}
    report = check(cfg, loaded.docs, loaded.registry, run_commands=bool(spec.get("run_commands", False)))
    return {
        "errors": [],
        "claims": [c.as_dict() for c in report.claims],
        "summary": report.summary(),
        "totals": {"stale": len(report.stale), "warn": len(report.warnings), "claims": len(report.claims)},
    }

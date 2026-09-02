"""Git log provider: recent commits (private-only by default; author names may be sensitive)."""
from __future__ import annotations

import subprocess


def collect(cfg, spec):
    limit = int(spec.get("limit", 20))
    fmt = "%h%x1f%an%x1f%ad%x1f%s"
    proc = subprocess.run(
        ["git", "log", f"-{limit}", f"--format={fmt}", "--date=short"],
        cwd=cfg.root,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or "git log failed (not a git repository?)")
    commits = []
    for line in proc.stdout.splitlines():
        parts = line.split("\x1f")
        if len(parts) == 4:
            commits.append({"hash": parts[0], "author": parts[1], "date": parts[2], "subject": parts[3]})
    status = subprocess.run(["git", "status", "--porcelain"], cwd=cfg.root, capture_output=True, text=True, timeout=30)
    dirty = [l for l in status.stdout.splitlines() if l.strip()] if status.returncode == 0 else []
    return {"commits": commits, "dirty_files": len(dirty)}

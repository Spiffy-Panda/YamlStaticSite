"""Agent skill suite packaging.

The canonical skills ship inside the package (yss/skills/<name>/SKILL.md). `yss skills --install`
copies them into a repo's .claude/skills/ so Claude Code (and compatible agents) discover them;
`yss skills --check` reports drift after an upgrade. `yss init` installs them automatically.
"""
from __future__ import annotations

from pathlib import Path

from .config import PKG_DIR

PKG_SKILLS = PKG_DIR / "skills"
TARGET_DIR = Path(".claude") / "skills"


def list_skills() -> list[str]:
    if not PKG_SKILLS.is_dir():
        return []
    return sorted(p.parent.name for p in PKG_SKILLS.glob("*/SKILL.md"))


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8").replace("\r\n", "\n")


def check(root: Path) -> list[tuple[str, str]]:
    """Return (name, status) with status in ok | missing | differs."""
    out = []
    for name in list_skills():
        src = PKG_SKILLS / name / "SKILL.md"
        dst = root / TARGET_DIR / name / "SKILL.md"
        if not dst.is_file():
            out.append((name, "missing"))
        elif _read(src) != _read(dst):
            out.append((name, "differs"))
        else:
            out.append((name, "ok"))
    return out


def install(root: Path, force: bool = False) -> list[tuple[str, str]]:
    """Copy skills into <root>/.claude/skills/. Status: installed | updated | unchanged | kept.

    `kept` means a differing local copy exists and force was not given.
    """
    out = []
    for name in list_skills():
        src = PKG_SKILLS / name / "SKILL.md"
        dst = root / TARGET_DIR / name / "SKILL.md"
        text = _read(src)
        if dst.is_file():
            if _read(dst) == text:
                out.append((name, "unchanged"))
                continue
            if not force:
                out.append((name, "kept"))
                continue
            status = "updated"
        else:
            status = "installed"
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_text(text, encoding="utf-8", newline="\n")
        out.append((name, status))
    return out

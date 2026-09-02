"""GitHub Pages setup through the gh CLI.

  python -m yss pages-setup [--repo owner/name] [--dry-run] [--no-secrets] [--no-pages] [--run]

- Reads forbidden/flag strings from .yss/local.yaml and stores them as repository secrets
  YSS_FORBIDDEN_STRINGS / YSS_FLAG_STRINGS (semicolon separated) so the workflow can redact.
- Sets the Pages source to "GitHub Actions" (build_type=workflow) so .github/workflows/pages.yml deploys.
- Optionally triggers the workflow.
Nothing here prints secret values; they are masked.
"""
from __future__ import annotations

import json
import subprocess

from .config import Config
from .visibility import mask


class GhError(Exception):
    pass


def _gh(*args: str, input_text: str | None = None) -> subprocess.CompletedProcess:
    try:
        return subprocess.run(["gh", *args], capture_output=True, text=True, input=input_text, timeout=120)
    except FileNotFoundError as exc:
        raise GhError("gh CLI not found; install from https://cli.github.com/") from exc


def _require(proc: subprocess.CompletedProcess, what: str) -> str:
    if proc.returncode != 0:
        raise GhError(f"{what} failed: {(proc.stderr or proc.stdout).strip()[:400]}")
    return proc.stdout.strip()


def current_repo() -> str:
    return _require(_gh("repo", "view", "--json", "nameWithOwner", "--jq", ".nameWithOwner"), "gh repo view")


def setup(
    cfg: Config,
    repo: str | None = None,
    dry_run: bool = False,
    secrets: bool = True,
    pages: bool = True,
    run_workflow: bool = False,
    workflow: str = "pages.yml",
) -> list[str]:
    log: list[str] = []
    _require(_gh("auth", "status"), "gh auth status")
    repo = repo or current_repo()
    log.append(f"repository: {repo}")

    if secrets:
        local = cfg.local_data()
        red = cfg.data["redaction"]
        values = {
            red["env_forbidden"]: ";".join(str(s) for s in local.get("forbidden_strings") or [] if s),
            red["env_flag"]: ";".join(str(s) for s in local.get("flag_strings") or [] if s),
        }
        for name, value in values.items():
            if not value:
                log.append(f"secret {name}: nothing configured in {red['local_file']}, skipped")
                continue
            shown = ", ".join(mask(v) for v in value.split(";"))
            if dry_run:
                log.append(f"secret {name}: would set ({shown})")
                continue
            _require(_gh("secret", "set", name, "--repo", repo, input_text=value), f"gh secret set {name}")
            log.append(f"secret {name}: set ({shown})")

    if pages:
        if dry_run:
            log.append("pages: would set source to GitHub Actions")
        else:
            create = _gh("api", "-X", "POST", f"repos/{repo}/pages", "-f", "build_type=workflow")
            if create.returncode != 0:
                update = _gh("api", "-X", "PUT", f"repos/{repo}/pages", "-f", "build_type=workflow")
                if update.returncode != 0:
                    raise GhError(
                        "enabling Pages failed (is the repository public, or on a plan with private Pages?):\n"
                        + (create.stderr or create.stdout).strip()[:300]
                    )
            info = _gh("api", f"repos/{repo}/pages")
            if info.returncode == 0:
                try:
                    data = json.loads(info.stdout)
                    log.append(f"pages: source=GitHub Actions url={data.get('html_url')}")
                except json.JSONDecodeError:
                    log.append("pages: enabled")
            else:
                log.append("pages: enabled")

    if run_workflow:
        if dry_run:
            log.append(f"workflow: would run {workflow}")
        else:
            _require(_gh("workflow", "run", workflow, "--repo", repo), f"gh workflow run {workflow}")
            log.append(f"workflow: {workflow} triggered; watch with `gh run watch`")
    return log

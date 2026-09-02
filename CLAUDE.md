# YamlStaticSite (yss)

Python toolchain that renders agent-first structured YAML (`docs/`) into a human-first static site
with a redacted **public** target (GitHub Pages) and a full **private** target. This repository is
also the pilot: its own plan, design, code map and decisions are structured docs.

## Non-negotiables

- Project knowledge goes in `docs/*.yaml` (schema-validated), never in new markdown files. Plan → `docs/plan.yaml`, architecture → `docs/design.yaml`, repo tour → `docs/codemap.yaml`, decisions → `docs/decisions.yaml`.
- Run `python -m yss validate` after editing any YAML; run `python -m yss build --target all --no-dynamic` after editing pages or prefabs.
- Anything not for the public site is `visibility: private` or under `private_notes`. No absolute paths in anything that reaches `dist/`.
- Forbidden strings live only in gitignored `.yss/local.yaml` or CI secrets, never in committed files.

## Skills

Load `yss` first (mental model + CLI), then `yss-doc`, `yss-page`, `yss-prefab` or `yss-publish` for the task at hand. They live in `.claude/skills/`.

## Quick commands

```bash
python -m yss validate
python -m yss ls
python -m yss query plan.milestones --where status=active --fields id,title
python -m yss build --target all --no-dynamic
python -m yss serve            # private :8800, public preview :8801/YamlStaticSite/
python -m unittest discover -s tests -v
```

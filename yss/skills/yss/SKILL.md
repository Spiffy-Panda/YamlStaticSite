---
name: yss
description: Overview of the yss toolchain (YAML static site) - the mental model, file layout, CLI cheat sheet and which sub-skill to load. Use whenever a task touches docs/*.yaml, site/pages, prefabs, site.yaml, building or serving the site, or when asked to "update the plan/design/code map/decisions".
---

# yss - agent-first docs, human-first site

Installed? `yss --version` (pip) or `python -m yss --version` (vendored). Not installed: `pip install git+https://github.com/Spiffy-Panda/YamlStaticSite`, then `yss init --name <project>` in a fresh repo.

**Rule zero: the structured YAML files are the source of truth. Never write a PLAN.md, DESIGN.md,
ARCHITECTURE.md or decision-log markdown; edit `docs/*.yaml` instead, then validate.**

## Mental model

| Layer | Where | Schema | Who edits |
|---|---|---|---|
| Structured docs (facts) | `docs/*.yaml` | `doc.<kind>` (envelope + kind) | agents, constantly |
| Pages (human framing) | `site/pages/*.yaml` | `page` | agents when a new view is needed |
| Prefabs (UI pieces) | `yss/prefabs/` built-in, `site/prefabs/` site-local | `prefab` | rarely; reuse first |
| Site config | `site.yaml` | `site` | rarely |
| Output | `dist/<target>/` | - | never (generated) |

Two targets: **public** (redacted, base_url `/RepoName/`, GitHub Pages) and **private** (everything, `/`).
Anything with `visibility: private` or under `private_notes` vanishes from public builds; the public
build then scans its own output for forbidden strings and deletes itself on a hit.

## CLI cheat sheet (run from the repo root)

```bash
python -m yss validate                       # schema-check everything; do this after every edit
python -m yss ls [docs|pages|prefabs|kinds|dynamic]
python -m yss schema doc.plan --yaml         # print any schema (doc.<kind>, page, prefab, site)
python -m yss query plan.milestones --where status=active --fields id,title   # read data like a page does
python -m yss new doc <kind> <id>            # scaffold from schema; also: new page <id> [--doc <id>], new prefab <name>
python -m yss build --target private --no-dynamic   # fast render check; --target all for both
python -m yss serve                          # private :8800, public preview :8801, rebuilds on change
python -m yss scan                           # forbidden/flagged strings in the source tree
python -m yss pages-setup --dry-run          # GitHub secrets + Pages source via gh (see yss-publish)
python -m yss skills                         # are this repo's copies of these skills current? (--install --force refreshes)
python -m unittest discover -s tests -v
```

## Workflow for any change

1. `python -m yss ls` to see what exists; `python -m yss schema <name> --yaml` for the exact fields.
2. Edit YAML. Keep ids stable, lowercase slugs. Fill `verify` on plan tasks. Mark private things.
3. `python -m yss validate` - fix every reported `file: at path: message`.
4. `python -m yss build --target all --no-dynamic` - fixes binding/prefab errors (page, section and prefab are named).
5. If the human needs to see it: `python -m yss serve` and open the private port.

## Sub-skills

- `yss-doc` - authoring structured docs (kinds, envelope, visibility, private notes).
- `yss-page` - pages, sections and the binding language (`from/where/sort/limit/map/group_by`).
- `yss-prefab` - writing or overriding prefabs (params, Jinja template, CSS, examples).
- `yss-publish` - targets, redaction, dynamic sources, the server and GitHub Pages.

## Things that bite

- A doc field bound by a page must exist; a missing field is a build error naming the page and section.
- Binding a private doc from a public page fails; mark the section `visibility: private`.
- No absolute paths in anything that reaches `dist/` (the public build forbids the checkout path).
- `map` values that contain `{{` are Jinja; everything else is a field name or literal.
- Dates stay strings (`2026-09-02`); the loader never converts them.

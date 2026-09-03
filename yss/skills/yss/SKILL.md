---
name: yss
description: Overview of the yss toolchain (YAML static site) - the mental model, file layout, CLI cheat sheet and which sub-skill to load. Use whenever a task touches docs/*.yaml, site/pages, prefabs, collections, site.yaml, evidence checks, building or serving the site, or when asked to "update the plan/design/code map/decisions".
---

# yss - agent-first docs, human-first site

Installed? `yss --version` (pip) or `python -m yss --version` (vendored). Not installed: `pip install git+https://github.com/Spiffy-Panda/YamlStaticSite`, then `yss init --name <project>` in a fresh repo.

**Rule zero: the structured YAML files are the source of truth. Never write a PLAN.md, DESIGN.md,
ARCHITECTURE.md or decision-log markdown; edit `docs/*.yaml` instead, then validate and check.**

## Mental model

| Layer | Where | Schema | Who edits |
|---|---|---|---|
| Structured docs (facts) | `docs/*.yaml` | `doc.<kind>` (envelope + kind) | agents, constantly |
| Pages (human framing) | `site/pages/*.yaml` | `page` | agents when a new view is needed |
| Prefabs (UI pieces) | `yss/prefabs/` built-in, `site/prefabs/` site-local | `prefab` | rarely; reuse first |
| Collections (a musing) | `<root>/<name>/{collection.yaml,hooks.py,docs,pages,prefabs,assets}` | `collection` | per topic |
| Site config | `site.yaml` | `site` | rarely |
| Archive | `docs/_archive/` (never loaded) | - | when work is done and committed |
| Output | `dist/<target>/` | - | never (generated) |

Two targets: **public** (redacted, base_url `/RepoName/`, GitHub Pages) and **private** (everything, `/`).
Anything with `visibility: private` or under `private_notes` vanishes from public builds; the public
build then scans its own output for forbidden strings and deletes itself on a hit.

**Docs prove themselves.** Paths and commands a doc cites are *evidence*; `yss check` fails when they
no longer hold, the build stamps `stale`/`warn` badges, and there is no `draft` status. Vocabularies
and length limits are data in site.yaml (overridable per collection), not hard-coded.

## CLI cheat sheet (run from the repo root)

```bash
python -m yss validate                       # schema, vocabulary, limit and reference checks; do this after every edit
python -m yss check [-v] [--run-commands|--no-run-commands] [--git|--no-git]   # evidence; exit 1 on stale
python -m yss refs plan#m3-pilot             # who references a doc or item
python -m yss ls [docs|pages|prefabs|kinds|dynamic|collections]
python -m yss schema doc.plan --yaml         # print any schema (doc.<kind>, page, prefab, collection, site)
python -m yss query plan.milestones --where status=active --fields id,title   # read data like a page does
python -m yss new doc <kind> <id>            # scaffold from schema; also: new page <id> [--doc <id>], new prefab <name>
python -m yss build --target private --no-dynamic   # fast render check; --target all for both
#   --strict / --no-strict override site.yaml build.strict (stale evidence and flagged strings fail)
python -m yss serve                          # private :8800, public preview :8801, rebuilds on change
python -m yss scan                           # forbidden/flagged strings in the source tree
python -m yss pages-setup --dry-run          # GitHub secrets + Pages source via gh (see yss-publish)
python -m yss skills                         # are this repo's copies of these skills current? (--install --force refreshes)
python -m unittest discover -s tests -v
```

## Workflow for any change

1. `python -m yss ls` to see what exists; `python -m yss schema <name> --yaml` for the exact fields.
2. Edit YAML. Keep ids stable, lowercase slugs. Fill `verify` and, where a path or command can prove it, `evidence:` on plan tasks. Mark private things.
3. `python -m yss validate` - fix every reported `file: at path: message` (schema, vocabulary, limit, dangling reference).
4. `python -m yss check` - fix the doc or the code for every stale claim; bump `updated` when a doc is re-verified.
5. `python -m yss build --target all --no-dynamic` - fixes binding/prefab errors (page, section and prefab are named).
6. If the human needs to see it: `python -m yss serve` and open the private port.
7. When a milestone is done and committed, move it to `docs/_archive/` and add a changelog line.
8. When only the human can decide something, write a `worksheet` doc (see `yss-doc`) instead of guessing.

## Sub-skills

- `yss-doc` - authoring structured docs (kinds, envelope, evidence, references, vocabularies, archive, worksheets).
- `yss-page` - pages, sections, the binding language, collection-local ids, evidence and collection prefabs.
- `yss-prefab` - writing or overriding prefabs (params, Jinja template, CSS, examples).
- `yss-publish` - targets, redaction, collections and hooks, mounts, dynamic sources, the server and GitHub Pages.

## Things that bite

- A doc field bound by a page must exist; a missing field is a build error naming the page and section.
- Binding a private doc from a public page fails; mark the section `visibility: private`.
- Inside a collection, `plan` means the collection's own plan; write `/plan` to reach the root doc.
- Text like `[[doc#item]]` is a reference and is validated; wrap it in backticks when you only mean the syntax.
- No absolute paths in anything that reaches `dist/` (the public build forbids the checkout path).
- `map` values that contain `{{` are Jinja; everything else is a field name or literal.
- Dates stay strings (`2026-09-02`); the loader never converts them. YAML turns bare `on`/`off`/`yes`/`no` into booleans - quote them.

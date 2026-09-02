# YamlStaticSite

Agent-first structured docs, human-first static site.

Agents keep a project's plan, design, code map, decisions, glossary and changelog as typed YAML
under `docs/`, validated by JSON Schema and **checked against the repo**: paths, symbols and commands
a doc cites are evidence, and stale claims fail `yss check` and show as badges. Pages under `site/pages/` frame that data for humans by
binding it into reusable UI prefabs (cards, boards, tables, timelines). One build produces a
redacted **public** site for GitHub Pages and a full **private** site for local use, plus the same
data as JSON. Folder-per-topic repositories (musings) become *collections* with their own config and
hooks. A two-port dev server previews both targets. This repository documents itself with the tool.

## Quick start

```bash
pip install -r requirements.txt        # PyYAML, Jinja2, jsonschema, markdown-it-py
python -m yss validate                 # check every YAML file against its schema
python -m yss build                    # dist/public and dist/private
python -m yss serve                    # http://127.0.0.1:8800/  (private)  http://127.0.0.1:8801/YamlStaticSite/  (public preview)
```

## Install and use in another repository

```bash
pip install git+https://github.com/Spiffy-Panda/YamlStaticSite   # or: pip install -e path/to/checkout
cd my-repo
yss init --name MyProject     # site.yaml, docs/plan.yaml, site/pages/index.yaml, CLAUDE.md, .claude/skills/yss*, .yss/local.example.yaml
yss serve
```

`yss init` also installs the agent skill suite into `.claude/skills/`; after upgrading yss run
`yss skills --check` (and `yss skills --install --force` to refresh). Without pip, vendor the `yss/`
folder and use `python -m yss` instead of `yss`.

**Telling an agent to set it up** - paste into the agent's prompt:

> Install yss with `pip install git+https://github.com/Spiffy-Panda/YamlStaticSite`, run `yss init --name <project>`
> in the repo root, then load the `yss` skill from `.claude/skills/yss/SKILL.md` and follow it. Project knowledge
> goes in `docs/*.yaml`, not markdown.

## Layout

```
site.yaml            site config: name, targets (base_url, redact), dynamic sources, serve ports
docs/                structured docs   (schema: doc.<kind>)   <- agents edit these
docs/_archive/       done-and-committed milestones; `_` paths are never loaded
examples/demo-musing/  a collection: collection.yaml, hooks.py, docs/, pages/, assets/, play/
site/pages/          un-inflated pages (schema: page)         <- human framing + bindings
site/prefabs/        site-local prefabs (schema: prefab)      <- override/extend built-ins
site/assets/         static files, prototypes/
schemas/             site-local schema additions (doc.<kind>.schema.yaml)
yss/                 the toolchain: schemas/, prefabs/, templates/, assets/, providers/
.claude/skills/      agent skills: yss, yss-doc, yss-page, yss-prefab, yss-publish
.yss/local.yaml      gitignored forbidden/flagged strings for the redaction scan
dist/<target>/       output (gitignored)
```

## Commands

| Command | Purpose |
|---|---|
| `python -m yss validate` | Schema, vocabulary, limit and reference checks for docs, pages, prefabs, collections, site.yaml |
| `python -m yss check [--run-commands] [--strict]` | Evidence: cited paths, globs, symbols, commands, git recency; exit 1 on stale |
| `python -m yss refs <doc>#<item>` | Inbound references to a doc or item |
| `python -m yss build [--target public\|private\|all] [--no-dynamic] [--strict]` | Render, export JSON, collect dynamic data, scan for leaks |
| `python -m yss serve [--no-watch]` | Serve both targets, rebuild on change, live dynamic refresh on the private port |
| `python -m yss dynamic [name]` | Re-collect dynamic sources into an existing build |
| `python -m yss scan` | Find forbidden/flagged strings in the source tree |
| `python -m yss ls [docs\|pages\|prefabs\|kinds\|dynamic\|collections]` | Inventory (kinds also prints the vocabularies) |
| `python -m yss query <doc>.<path> [--where k=v] [--sort f] [--fields a,b]` | Read data the way pages do |
| `python -m yss schema <name> [--yaml]` | Print a schema |
| `python -m yss new doc\|page\|prefab ...` | Scaffold from schema |
| `python -m yss skills [--install] [--force]` | Check or install the agent skills into `.claude/skills/` |
| `python -m yss pages-setup [--dry-run] [--run]` | Via gh: redaction secrets from `.yss/local.yaml`, Pages source = GitHub Actions |
| `python -m unittest discover -s tests -v` | Tests |

## License

MIT, see [LICENSE](LICENSE).

## Privacy model

Objects marked `visibility: private` and every `private_notes` key are removed before a public
render. The public build then scans all of its output for forbidden strings (from `.yss/local.yaml`
or the `YSS_FORBIDDEN_STRINGS` secret, plus the absolute checkout path) and deletes itself if any
appear. Flagged strings only warn.

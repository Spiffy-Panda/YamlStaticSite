---
name: yss-publish
description: Build, preview, redact and publish a yss site - targets (public/private), the forbidden-string scan, evidence checks in CI, collections (folder-per-musing) with collection.yaml and hooks.py, mounts for playables, dynamic data sources and providers, pluggable markdown, the two-port dev server and the GitHub Pages workflow. Use when asked to build/serve/deploy the site, add a collection or musing, write a hook, host a Godot/wasm prototype, add runtime data (tests, git, metrics), or check for leaks before publishing.
---

# Building, serving, publishing

## Targets

`site.yaml` → `targets`. Defaults: `public` (`base_url: /RepoName/`, `redact: true`) and `private`
(`base_url: /`, `redact: false`). Output is `dist/<target>/`, gitignored.

```bash
python -m yss build                       # both targets, with dynamic sources
python -m yss build --target public       # one target
python -m yss build --no-dynamic          # skip runtime collection (fast)
python -m yss build --strict              # flagged strings and stale evidence fail too
python -m yss build --no-strict           # never fail on them, whatever site.yaml says
python -m yss check --run-commands        # evidence including command claims (slow); exit 1 on stale
```

**Flow policy is config; flags override it.** `build.strict` in site.yaml decides whether a build
fails on flagged strings and stale evidence. `evidence.git_recency` and `evidence.run_commands` are
site-wide and may be overridden per collection in its `collection.yaml`. Resolution order for every
doc: CLI flag (`--strict/--no-strict`, `--git/--no-git`, `--run-commands/--no-run-commands`), then
that doc's collection.yaml, then site.yaml, then the defaults in `yss/config.py`. Keep the Pages
workflow's explicit `--strict` so the deploy gate does not depend on config.

Exit 1 with `file: at path: message` lines on validation errors (schema, vocabulary, limits, dangling
references), or the page/section/prefab on render errors. A redacting target that contains a
**forbidden string deletes its output** and fails. Every build stamps freshness from the fast
evidence checks (paths, globs, symbols, git recency) and writes `data/evidence.json`.

## Redaction

- Mark data: `visibility: private` on collections (collection.yaml), docs, pages, sections or any list item; `private_notes` on anything. Filtered before rendering.
- Forbidden and flagged strings live in gitignored `.yss/local.yaml` (`forbidden_strings`, `flag_strings`), or in CI as `YSS_FORBIDDEN_STRINGS` / `YSS_FLAG_STRINGS` (semicolon separated). Matching is case-insensitive substring; console output masks matches.
- The absolute checkout path is always forbidden in redacting targets (`redaction.forbid_root_path`).
- `python -m yss scan` checks the **source tree** before a commit; the build checks the **output**.
- Never put the forbidden strings themselves in a committed file.

## Collections (folder-per-musing)

```yaml
# site.yaml
collections:
  - root: musings/*            # every matching folder is a collection named after the folder
```

Inside a collection folder: `docs/`, `pages/`, `prefabs/`, `schemas/`, `assets/`, optional
`collection.yaml` and `hooks.py`. Doc ids become `<folder>/<id>`, pages live under `/<folder>/`,
the nav shows one link per collection, and pages get a collection bar with emblem, theme and sub-nav.
Anything the YAML cannot express goes in that folder's `hooks.py` - never in yss.

```yaml
# <folder>/collection.yaml  (schema: python -m yss schema collection --yaml)
title: Demo musing
summary: One paragraph.
emblem: "🧪"                     # or assets/emblem.svg
order: 1
hero: true                      # sit in the parent page's hero panel above the ordinary cards
links:                          # the card contract: what a parent site shows for this collection
  - {label: Overview, href: "", kind: page}          # relative to /<folder>/
  - {label: The playable, href: "play/index.html", kind: play}   # kind play renders as a button
visibility: public              # private -> the whole collection is absent from public builds
theme: {accent: "#7a3e9d", css: [assets/theme.css]}
vocabularies: {risk_status: [open, watching, resolved]}   # this collection's own words
limits: {summary: 400}
evidence: {git_recency: true, run_commands: false}         # this collection's evidence policy
dynamic: {sources: {notes: {provider: hooks:notes, targets: [public, private]}}}
mounts: [{path: play, at: play/, targets: [private, public]}]   # copied to /<folder>/play/
```

**The card contract.** A parent page never hand-lists its sub-sites: `emblem`, `summary`, `order`,
`hero` and `links` in each `collection.yaml` are carried on `$collections`, and the built-in
`collection-index` prefab draws the hero panel and the grid from them, so a migrated collection
appears by existing. A relative `href` resolves under that collection's route (`play/index.html`
reaches its mount); a link into a mount the target does not carry is dropped, so the public site
never advertises a private playable. Extend the contract upstream rather than writing card markup
in a page.

**Mounts are private by default.** A mount with no `targets` exists only in the private build; a
collection publishes one by naming the public target on that mount, one mount at a time. Never flip
a whole site to public mounts - a build in progress would land on Pages. The demo collection's
playable is the worked example. A public mount's files go through the redaction scan like any other
output, so no absolute paths and nothing personal inside the mounted folder.

```python
# <folder>/hooks.py - every function optional; runs in-process with the repo root on sys.path
def configure(collection, cfg): ...            # adjust collection.yaml data; return the dict
def load_docs(collection, cfg): return [...]   # extra docs generated from data (dicts with kind/title/...)
def load_pages(collection, cfg): return [...]  # extra pages
def markdown(text): return html                # this collection's own markdown renderer
def before_render(cfg, target, collection): ...   # e.g. build a playable from source
def after_build(cfg, target, out_dir, collection): ...   # e.g. copy generated artefacts into out_dir/<id>/
providers = {"notes": lambda cfg, spec: {...}}  # dynamic sources: provider: hooks:notes
```

Site-wide equivalents: `site.yaml` `hooks: hooks.py`, `mounts:`, and `markdown: {renderer: module:function}`
to replace markdown-it-py everywhere (use it when the host repo already has a documented markdown subset).

Adopting yss in a repo that has its own site generator: migrate one collection at a time; link the
un-migrated parts from a markdown section until they move; retire the old generator last.
`examples/demo-musing/` in the yss repo is a complete working collection to copy from.

## Dynamic sources (runtime JSON)

Declared in `site.yaml` (or a collection.yaml, namespaced `<collection>.<name>` but referenced by the short name from that collection's pages):

```yaml
dynamic:
  sources:
    testruns:  {provider: yss.providers.testruns:collect, tests_dir: tests, targets: [private], ttl: 60}
    evidence:  {provider: yss.providers.evidence:collect, run_commands: true, targets: [private], on_build: false}
    metrics:   {command: "python tools/metrics.py", targets: [public, private], on_build: true, timeout: 60}
    coverage:  {file: reports/coverage.json}
```

- Collected at build into `dist/<target>/dynamic/<name>.json` as `{source, collected_at, ok, data}` or `{..., ok: false, error}`.
- `targets` limits where a source exists; a page section pointing at a missing source shows its `empty` text (no failure).
- A provider is `collect(cfg, spec) -> JSON-able` in any importable module, or `hooks:<name>`. Return relative paths only.
- Built-ins: `yss.providers.buildinfo`, `yss.providers.testruns` (unittest in a subprocess), `yss.providers.gitlog` (private-only by default: author names), `yss.providers.evidence`.
- Refresh without a full build: `python -m yss dynamic [name] --target private`.
- Sections render them with `type: dynamic` and `view: table|kv|list|cards|json|custom` (see `yss-page`).

## Server

```bash
python -m yss serve                       # private http://127.0.0.1:8800/ , public preview http://127.0.0.1:8801/RepoName/
python -m yss serve --no-watch --no-build # just serve what is in dist/
```

- Rebuilds both targets when `site.yaml`, `docs/`, `site/`, `schemas/`, collection folders (and `serve.watch` extras) change; a failed build writes an error page.
- Private port serves a stale dynamic source immediately and re-collects it in the background (stale-while-revalidate; `ttl` per source). `?refresh=1` (the refresh button) waits for a fresh collection. A missing file is collected synchronously.
- Both ports send `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` (`serve.coop_coep`). **Export wasm and Godot prototypes without threads** so they need no isolation headers: GitHub Pages cannot send any, and nothing may depend on the private server's. The coi-serviceworker shim was considered and rejected. A Godot web export is also tens of megabytes and Pages bandwidth is metered against the account, so keep such a mount private until that cost has been decided deliberately.
- Prototypes: put the export in a collection and mount it (`mounts`), or copy it into `site/assets/prototypes/<name>/` and add an `embed` section.

## GitHub Pages

`.github/workflows/pages.yml` validates, checks evidence, tests, builds `public` with the secrets as
env vars and deploys `dist/public`. Keep `targets.public.base_url` equal to `/<repo name>/` (or `/`
for a user/organisation site) and `site.repo` equal to the repository URL.

One-time setup with the gh CLI (needs `gh auth login` with `repo` and `workflow` scopes):

```bash
gh repo create <owner>/<name> --public --source . --remote origin --push   # first push (Pages needs a public repo on free plans)
python -m yss pages-setup --dry-run                                        # shows what will change, secrets masked
python -m yss pages-setup                                                  # secrets from .yss/local.yaml + Pages source = GitHub Actions
python -m yss pages-setup --run && gh run watch                            # also trigger and watch the workflow
```

`pages-setup` stores `YSS_FORBIDDEN_STRINGS` / `YSS_FLAG_STRINGS` as repository secrets (semicolon
joined from `.yss/local.yaml`) and calls the Pages API with `build_type=workflow`. Secrets and Pages
are account settings: run it only when the human has asked for publishing. Re-run it whenever
`.yss/local.yaml` changes.

## Pre-publish checklist

1. `python -m yss validate`
2. `python -m yss check` (and `--run-commands` when task verify commands matter)
3. `python -m unittest discover -s tests`
4. `python -m yss scan`
5. `python -m yss build --target public` (must succeed; reads `.yss/local.yaml`)
6. Open the public preview port and click through the nav; private-only pages and collections must be absent.

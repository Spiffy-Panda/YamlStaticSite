---
name: yss-publish
description: Build, preview, redact and publish a yss site - targets (public/private), the forbidden-string scan, dynamic data sources and providers, the two-port dev server, prototype hosting headers, and the GitHub Pages workflow. Use when asked to build/serve/deploy the site, add runtime data (tests, git, metrics), host a Godot/wasm prototype, or check for leaks before publishing.
---

# Building, serving, publishing

## Targets

`site.yaml` → `targets`. Defaults: `public` (`base_url: /RepoName/`, `redact: true`) and `private`
(`base_url: /`, `redact: false`). Output is `dist/<target>/`, gitignored.

```bash
python -m yss build                       # both targets, with dynamic sources
python -m yss build --target public       # one target
python -m yss build --no-dynamic          # skip runtime collection (fast)
python -m yss build --strict              # flagged strings fail too
```

Exit 1 with a list of `file: at path: message` on validation errors, or the page/section/prefab on
render errors. A redacting target that contains a **forbidden string deletes its output** and fails.

## Redaction

- Mark data: `visibility: private` on docs, pages, sections or any list item; `private_notes` on anything. Filtered before rendering.
- Forbidden and flagged strings live in gitignored `.yss/local.yaml` (`forbidden_strings`, `flag_strings`), or in CI as `YSS_FORBIDDEN_STRINGS` / `YSS_FLAG_STRINGS` (semicolon separated). Matching is case-insensitive substring; console output masks matches.
- The absolute checkout path is always forbidden in redacting targets (`redaction.forbid_root_path`).
- `python -m yss scan` checks the **source tree** (docs, site, README...) before a commit; the build checks the **output**.
- Never put the forbidden strings themselves in a committed file.

## Dynamic sources (runtime JSON)

Declared in `site.yaml`:

```yaml
dynamic:
  sources:
    testruns:  {provider: yss.providers.testruns:collect, tests_dir: tests, targets: [private], ttl: 60}
    metrics:   {command: "python tools/metrics.py", targets: [public, private], on_build: true, timeout: 60}
    coverage:  {file: reports/coverage.json}
```

- Collected at build into `dist/<target>/dynamic/<name>.json` as `{source, collected_at, ok, data}` or `{..., ok: false, error}`.
- `targets` limits where a source exists; a page section pointing at a missing source shows its `empty` text (no failure).
- A provider is `collect(cfg, spec) -> JSON-able` in any importable module. Return relative paths only.
- Built-ins: `yss.providers.buildinfo`, `yss.providers.testruns` (unittest in a subprocess), `yss.providers.gitlog` (private-only by default: author names).
- Refresh without a full build: `python -m yss dynamic [name] --target private`.
- Sections render them with `type: dynamic` and `view: table|kv|list|cards|json|custom` (see `yss-page`).

## Server

```bash
python -m yss serve                       # private http://127.0.0.1:8800/ , public preview http://127.0.0.1:8801/RepoName/
python -m yss serve --no-watch --no-build # just serve what is in dist/
```

- Rebuilds both targets when `site.yaml`, `docs/`, `site/`, `schemas/` (and `serve.watch` extras) change; a failed build writes an error page.
- Private port serves a stale dynamic source immediately and re-collects it in the background (stale-while-revalidate; `ttl` per source). `?refresh=1` (the refresh button) waits for a fresh collection. A missing file is collected synchronously.
- Both ports send `Cross-Origin-Opener-Policy: same-origin` and `Cross-Origin-Embedder-Policy: require-corp` (`serve.coop_coep`), which threaded wasm and Godot 4 web exports need. GitHub Pages cannot; export Godot without threads or add a coi-serviceworker shim beside the export.
- Prototypes: copy the export into `site/assets/prototypes/<name>/` and add an `embed` section.

## GitHub Pages

`.github/workflows/pages.yml` validates, tests, builds `public` with the secrets as env vars and
deploys `dist/public`. Keep `targets.public.base_url` equal to `/<repo name>/` (or `/` for a
user/organisation site) and `site.repo` equal to the repository URL.

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
`.yss/local.yaml` changes. Manual equivalent: Settings → Pages → Source: GitHub Actions; Settings →
Secrets → Actions → the two names above.

## Pre-publish checklist

1. `python -m yss validate`
2. `python -m unittest discover -s tests`
3. `python -m yss scan`
4. `python -m yss build --target public` (must succeed; reads `.yss/local.yaml`)
5. Open the public preview port and click through the nav; private-only pages must be absent.

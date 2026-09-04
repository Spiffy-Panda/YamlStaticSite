---
name: yss-page
description: Author or change yss pages (site/pages/*.yaml or a collection's pages/) - routes, sections, human framing text, and bindings that fill prefabs from structured docs, including worksheets, evidence lists and collection indexes. Use when asked to show/present/visualise doc data, add a page or section, add a card grid/board/table/timeline, embed a prototype, render a worksheet, or display runtime data.
---

# Authoring pages

A page is the **human framing**: it chooses which data to show, in what order, with what
explanation. It holds almost no facts of its own; facts live in `docs/`. Schema: `python -m yss schema page --yaml`.

```yaml
id: plan                    # file stem; `index` -> route / (or /<collection>/ inside a collection)
route: /plan/               # pretty URL, trailing slash; collections prefix it automatically
title: Plan
summary: One sentence - who this page is for and what it answers.
visibility: public
nav: {label: Plan, order: 10, group: content}   # group must be one site.yaml nav.groups declares, or the build warns
docs: [plan]                       # doc ids presented here (enables doc_url links and the freshness badge)
sections: [...]
```

`nav.hidden: true` keeps a page out of the bar entirely. `nav.group` must name a group declared in
site.yaml's `nav.groups`, or the build warns and the page is drawn in the first declared group -
or, if none is declared, in a trailing unlabelled one. Collection pages are exempt: the loader
gives them `group = collection title` and the collection's own sub-nav draws them.

Inside a collection, `plan` means the collection's own plan doc; `/plan` reaches the root doc.
Relative `src`, `head.css` and `include` paths resolve against the collection folder first.

## Section types

```yaml
- id: goals                        # anchor; defaults to section-N
  type: prefab
  heading: Goals                   # optional; headings feed the on-page TOC
  intro: Markdown shown under the heading. Put the *why* here.   # optional framing; [[doc#item]] links work
  visibility: private              # optional; drops the section from public builds
  prefab: bullet-list
  args:
    items: {from: plan.goals}      # a binding (object with `from`) ...
    ordered: false                 # ... or a literal

- type: markdown
  markdown: |
    Prose. Or use `from: design.overview` to pull a markdown field from a doc.
  jinja: true                      # optional: {{ url('...') }}, {{ docs.plan.title }} available

- type: dynamic                    # runtime JSON (see yss-publish for sources)
  source: testruns                 # a collection page may name its own collection.yaml sources
  view: table                      # table | kv | list | cards | json | custom
  path: cases                      # dotted path inside the source's `data`
  columns: [name, {key: status, label: Status}]
  empty: Shown when the source is not in this build.

- type: embed                      # prototypes; use a mount (yss-publish) for a whole export folder
  kind: iframe                     # iframe | godot | wasm | image | video
  src: assets/prototypes/hello/index.html
  height: 480

- type: include                    # a file from the repo
  path: README.md
  as: markdown                     # markdown | html | text

- type: html
  html: "<p>raw html</p>"
```

## Bindings (the selection language)

```yaml
from: plan.milestones          # <doc id>.<dotted path>; /plan.milestones forces the root doc from a collection
                               # virtual roots: $docs, $pages, $site, $prefabs, $collections, $evidence
                               # design.$items: every item of every type array in one doc, each
                               #   tagged `_type: <array name>` - the only way one binding (and one
                               #   group_by) can span principles + components + constraints. Skips
                               #   the envelope lists (groups, links, evidence, tags, owners,
                               #   related) and `_`-prefixed metadata. Exact suffix only, no $items.0
where:                         # all conditions must hold
  status: [active, blocked]    # any-of
  priority: {lte: 2}           # not | contains | exists | gte | lte
  _collection: ""              # root docs only (doc metadata fields start with _)
sort: [-priority, title]       # string or list; leading - is descending
limit: 6
map:                           # rename/derive fields so they match prefab params; originals are kept
  badge: status                # field name (dotted paths allowed)
  body: summary
  href: "#{{ id }}"            # any string with {{ }} is a Jinja template over the item (+ url(), doc_url(), ref_url(), docs)
  meta: "{{ tasks | length }} tasks"
fields: [id, title]            # keep only these - runs BEFORE group_by, so a field group_by needs
                               #   must be listed here or every bucket collapses to None (the build
                               #   warns and names the page and section). `map` is safe: it keeps
                               #   the original fields.
group_by: status               # -> [{key, items}] (for status-board); when the source doc declares
                               #   `groups:`, each bucket also carries that group's title/blurb and
                               #   the declared order (for group-sections)
```

`python -m yss query 'design.$items' --where _type=components` reads exactly what a page would.
Two ids the same on one page is a build warning (`duplicate anchor id`) and fatal under `--strict`:
`group-sections` anchors a declared group under its own group id, so a page that renders one group
twice needs `id_prefix` on the second section.

Check what a binding returns before wiring it:

```bash
python -m yss query plan.milestones --where status=active --sort -priority --fields id,title
```

Items carry `_evidence: {status}` after the build; cards, tasks and modules show a stale/warn badge automatically.

## Which prefab

| Data shape | Prefab | Args |
|---|---|---|
| list of strings | `bullet-list` | `items` |
| list of items with title/summary | `card-grid` | `items` (map -> title, subtitle, body, badge, href, meta, tags), `columns` |
| grouped by status | `status-board` | `groups` (binding with `group_by`), `order` |
| plan milestones + tasks | `task-list` | `milestones` |
| any list of mappings | `table` | `rows`, `columns` (`{key, label, md: true}`) |
| a mapping | `kv` | `pairs` |
| dated entries | `timeline` | `items` (title, date, badge, body, items) |
| design flows | `steps` | `flows` |
| codemap modules | `module-list` | `modules` |
| decisions | `decision-list` | `entries` |
| glossary | `term-list` | `terms` |
| changelog | `changelog-list` | `releases` |
| all docs | `doc-index` | `docs: {from: $docs}` |
| all collections | `collection-index` | `collections: {from: $collections}` |
| evidence claims | `evidence-list` | `claims: {from: $evidence}`, `show_ok` |
| a worksheet doc | `worksheet` | `questions`, `prompt`, `intro`, `warn`, `title` (all `from: <worksheet-doc>.<field>`) |

`python -m yss ls prefabs` lists params (`*` = required); the Reference page renders every example.

## Rules

- Every section that shows data should have an `intro` or a page `summary` that says why a human is looking at it. That framing is the page's job.
- Never paste facts into `markdown` sections that belong in a doc; bind them.
- Binding a private doc or field from a public page is a build error; mark the section private.
- Routes are unique; `docs` must list real doc ids (validated, collection-local first).
- A worksheet page is the only place a human answers questions; keep it to one worksheet doc per page.
- After editing: `python -m yss validate && python -m yss build --target all --no-dynamic`.

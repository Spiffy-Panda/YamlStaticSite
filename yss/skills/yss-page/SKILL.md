---
name: yss-page
description: Author or change yss pages (site/pages/*.yaml) - routes, sections, human framing text, and bindings that fill prefabs from structured docs. Use when asked to show/present/visualise doc data, add a page or section, add a card grid/board/table/timeline, embed a prototype, or display runtime data.
---

# Authoring pages

A page is the **human framing**: it chooses which data to show, in what order, with what
explanation. It holds almost no facts of its own; facts live in `docs/`. Schema: `python -m yss schema page --yaml`.

```yaml
id: plan                    # file stem; `index` -> route /
route: /plan/               # pretty URL, trailing slash
title: Plan
summary: One sentence - who this page is for and what it answers.
visibility: public
nav: {label: Plan, order: 10}      # hidden: true to keep out of the nav
docs: [plan]                       # doc ids presented here (enables doc_url links)
sections: [...]
```

## Section types

```yaml
- id: goals                        # anchor; defaults to section-N
  type: prefab
  heading: Goals                   # optional; headings feed the on-page TOC
  intro: Markdown shown under the heading. Put the *why* here.   # optional framing
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
  source: testruns
  view: table                      # table | kv | list | cards | json | custom
  path: cases                      # dotted path inside the source's `data`
  columns: [name, {key: status, label: Status}]
  empty: Shown when the source is not in this build.

- type: embed                      # prototypes
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
from: plan.milestones          # <doc id>.<dotted path>; virtual roots: $docs, $pages, $site, $prefabs
where:                         # all conditions must hold
  status: [active, blocked]    # any-of
  priority: {lte: 2}           # not | contains | exists | gte | lte
sort: [-priority, title]       # string or list; leading - is descending
limit: 6
map:                           # rename/derive fields so they match prefab params; originals are kept
  badge: status                # field name (dotted paths allowed)
  body: summary
  href: "#{{ id }}"            # any string with {{ }} is a Jinja template over the item (+ url(), doc_url(), docs)
  meta: "{{ tasks | length }} tasks"
  fixed: 3                     # literal
fields: [id, title]            # keep only these
group_by: status               # -> [{key, items}] (for status-board)
```

Check what a binding returns before wiring it:

```bash
python -m yss query plan.milestones --where status=active --sort -priority --fields id,title
```

## Which prefab

| Data shape | Prefab | Args |
|---|---|---|
| list of strings | `bullet-list` | `items` |
| list of items with title/summary | `card-grid` | `items` (map → title, subtitle, body, badge, href, meta, tags), `columns` |
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

`python -m yss ls prefabs` lists params (`*` = required); the Reference page renders every example.

## Rules

- Every section that shows data should have an `intro` or a page `summary` that says why a human is looking at it. That framing is the page's job.
- Never paste facts into `markdown` sections that belong in a doc; bind them.
- Binding a private doc or field from a public page is a build error; mark the section private.
- Routes are unique; `docs` must list real doc ids (validated).
- After editing: `python -m yss validate && python -m yss build --target all --no-dynamic`.

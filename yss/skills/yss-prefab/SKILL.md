---
name: yss-prefab
description: Write, override or fix a yss UI prefab (Jinja2 template + params + CSS in YAML under site/prefabs/ or yss/prefabs/). Use when a page needs a visual component that no built-in prefab covers, when asked to restyle cards/tables/boards, or when a prefab example fails to render.
---

# Authoring prefabs

Reuse first: `python -m yss ls prefabs` and the Reference page show every prefab with params and a
rendered example. Most needs are covered by `card-grid` + `map` or `table` + `columns`.

Scaffold: `python -m yss new prefab <name>` (writes `site/prefabs/<name>.yaml`). A site prefab with
the same name as a built-in **replaces** it. Schema: `python -m yss schema prefab --yaml`.

```yaml
name: pill-list
description: One line - what it renders and when to use it.
category: list                 # layout | list | item | badge | data | media | nav | text
params:
  items: {type: list, required: true, item: {text: required, badge: optional}}
  compact: {type: boolean, default: false}
template: |
  <ul class="pill-list {% if compact %}compact{% endif %}">
  {% for it in items %}
    <li>{{ it.text | md_inline }}{% if it.badge %} {{ prefab('badge', text=it.badge) }}{% endif %}</li>
  {% endfor %}
  </ul>
css: |
  .pill-list { display: flex; gap: .4rem; flex-wrap: wrap; list-style: none; padding: 0; }
  .pill-list li { border: 1px solid var(--line); border-radius: 999px; padding: .1rem .6rem; }
examples:
  - args: {items: [{text: one}, {text: two, badge: done}]}
```

## Template environment

- Autoescaping is on. Filters: `md` (block markdown), `md_inline`, `slug`, `tojson`, plus all Jinja built-ins.
- Globals: `prefab(name, dict_or_kwargs)` to compose, `url(path)` for base-URL-safe links, `doc_url(doc_id)`, `site`, `target`, `docs`.
- Params with `required: true` and no default raise a build error naming the prefab and param; `default` fills missing values; `type` is checked loosely (string, markdown, integer, number, boolean, list, object, any).
- Extra args not declared in `params` are still passed to the template (items mapped from docs carry all their fields).
- Values may be Jinja `Undefined` when called from another template; test with `{% if x %}`.
- Fields named like dict methods (`items`, `keys`, `values`, `get`, `copy`) must be read with subscript: `it['items']`, never `it.items` (that returns the method).

## Styling

- CSS is concatenated into `assets/prefabs.css`; scope every rule under a class named after the prefab.
- Use the shared variables: `--bg`, `--bg-2`, `--fg`, `--fg-2`, `--line`, `--accent`, `--accent-2`, `--ok`, `--warn`, `--bad`, `--muted`, `--code`, `--radius`.
- Status colouring: add class `tone-<slug>` (done, active, blocked, planned, dropped, accepted, failed, ...) and read `var(--tone)`; `badge` and `card` already do this.
- Dark mode comes from the variables; never hard-code colours.

## Rules

- Keep logic out of templates: selection, sorting and renaming belong in the page binding (`where`, `sort`, `map`).
- Always ship at least one `examples` entry; the test suite renders every example and the Reference page shows it.
- Document list item fields under `params.<name>.item` so page authors know what to `map`.
- Verify: `python -m yss validate && python -m unittest tests.test_yss.PrefabTests -v`.

---
name: yss-doc
description: Author or update yss structured docs (docs/*.yaml) - plan, design, codemap, decisions, glossary, changelog, generic, or a new kind. Use when asked to record a plan/milestone/task, document architecture, map the code, log a decision, define terms, or note a release.
---

# Authoring structured docs

Docs live in `docs/<id>.yaml`. One doc per file; `id` defaults to the file stem. Every doc has the
**envelope** plus kind-specific fields. Print the exact schema before writing:

```bash
python -m yss schema doc.plan --yaml     # or doc.design, doc.codemap, doc.decisions, doc.glossary, doc.changelog, doc.generic
python -m yss new doc plan roadmap       # scaffold with required fields
```

## Envelope (all kinds)

```yaml
kind: plan                 # selects the schema
id: plan                   # slug, stable
title: Plan
summary: One paragraph, plain text.
visibility: public         # or private -> whole doc dropped from public builds
status: active             # draft | active | stable | deprecated | archived
owners: [agent]            # handles or roles, never legal names
updated: 2026-09-02
tags: [a, b]
related: [design]          # doc ids, validated
links: [{label: Issue 12, href: https://..., kind: issue}]
private_notes: anything    # stripped from public builds
agent_notes: Conventions for editing this doc.
x-anything: allowed        # experimental fields must start with x-
```

Any list item may also carry `visibility`, `private_notes`, `tags`, `links`, `notes`.

## Kinds at a glance

- **plan**: `goals[]`, `non_goals[]`, `milestones[] {id, title, status, priority 1-5, target_date, depends_on[], done_when, tasks[] {id, title, status, owner, estimate, depends_on[], done_when, verify}}`, `risks[] {id, title, likelihood, impact, mitigation, status}`, `open_questions[] {id, question, context, owner, status, answer}`. Status vocabulary: `planned | active | blocked | done | dropped`.
- **design**: `overview` (markdown), `principles[] {id, title, rationale}`, `components[] {id, name, kind, responsibility, inputs[], outputs[], depends_on[], code[], status}`, `interfaces[] {id, name, kind, signature, description, component}`, `flows[] {id, title, trigger, steps[] {actor, action, produces}}`, `constraints[] {id, text, source}`, `alternatives[] {id, option, rejected_because}`.
- **codemap**: `roots[] {path, purpose}`, `modules[] {id, path, purpose, language, status, exports[] {name, kind, signature, description}, depends_on[], tests[]}`, `entrypoints[] {id, name, command, module, description}`, `conventions[] {id, rule, rationale}`.
- **decisions**: `entries[] {id, date, title, status proposed|accepted|rejected|superseded|deprecated, context, decision, consequences[], alternatives[], supersedes, superseded_by}`. Append only; supersede instead of editing.
- **glossary**: `terms[] {id, term, definition (markdown), aliases[], see_also[]}`.
- **changelog**: `releases[] {version, date, status, summary, changes[] {type added|changed|fixed|removed|deprecated|security, text, refs[]}}`, newest first.
- **generic**: `shape` (a name for the data's shape) and `data` (any object/array). Escape hatch; promote to a real kind when it repeats.

## Rules

- Ids: lowercase slugs (`m3-pilot`, `adr-004`), unique within the doc, never renamed.
- Status changes are edits to the field, not new items. Keep `updated` current.
- Plan tasks carry `verify` - a command or an observation a reviewer can check.
- Markdown is allowed in fields documented as markdown (`overview`, `definition`, `context`, `decision`, `notes`); keep other fields plain.
- Private information: prefer `visibility: private` on the item or `private_notes`; the output scan is a backstop, not the plan.
- After editing: `python -m yss validate`, then `python -m yss query <doc>.<field>` to see what pages will get.

## Adding a new kind

1. Copy an existing schema to `schemas/doc.<kind>.schema.yaml` (site-local dir; overrides package schemas with the same name).
2. Set `properties.kind: {const: <kind>}`, describe the body, keep `additionalProperties: false`. Envelope fields and `$defs` (slug, visibility, link, item_base) are merged in automatically; use `allOf: [{$ref: "#/$defs/item_base"}]` plus `unevaluatedProperties: false` on list items.
3. `python -m yss ls kinds` should list it; `python -m yss new doc <kind> <id>` scaffolds it.
4. Present it with a page (see `yss-page`); the generic `table`, `card-grid` and `bullet-list` prefabs need no new prefab.

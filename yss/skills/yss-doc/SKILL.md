---
name: yss-doc
description: Author or update yss structured docs (docs/*.yaml or a collection's docs/) - plan, design, codemap, decisions, glossary, changelog, worksheet, generic, or a new kind - including evidence claims, cross-references, vocabularies and the archive rule. Use when asked to record a plan/milestone/task, document architecture, map the code, log a decision, define terms, note a release, or ask the human for a verdict.
---

# Authoring structured docs

Docs live in `docs/<id>.yaml` (or `<collection>/docs/<id>.yaml`, where the global id becomes
`<collection>/<id>`). One doc per file; `id` defaults to the file stem. Every doc has the
**envelope** plus kind-specific fields. Print the exact schema before writing:

```bash
python -m yss schema doc.plan --yaml     # doc.design, doc.codemap, doc.decisions, doc.glossary, doc.changelog, doc.worksheet, doc.generic
python -m yss ls kinds                   # kinds plus the current vocabularies
python -m yss new doc plan roadmap       # scaffold with required fields (placeholders are valid)
```

## Envelope (all kinds)

```yaml
kind: plan                 # selects the schema
id: plan                   # slug, stable
title: Plan                # <= limits.title chars (120)
summary: One paragraph, plain text.   # <= limits.summary (300)
visibility: public         # or private -> whole doc dropped from public builds
status: active             # lifecycle only: active | stable | deprecated | archived (no draft - evidence says if it is current)
owners: [agent]            # handles or roles, never legal names
updated: 2026-09-02        # bump when you re-verify; git recency compares cited code against it
tags: [a, b]
related: [design]          # doc ids, validated (collection-local first; /design forces the root doc)
links: [{label: Issue 12, href: https://..., kind: issue}]
evidence:                  # claims yss check proves (see below)
  - {path: yss/build.py, contains: "def build("}
private_notes: anything    # stripped from public builds
agent_notes: Conventions for editing this doc.
x-anything: allowed        # experimental fields must start with x-
```

Any list item may also carry `visibility`, `private_notes`, `tags`, `links`, `notes`, `evidence`.

## Evidence (what makes a doc trustworthy)

```yaml
evidence:
  - {path: yss/cli.py}                              # exists (globs allowed; also tried relative to the collection root)
  - {path: yss/cli.py, contains: "def main("}       # and contains the text
  - {glob: "yss/prefabs/*.yaml", min: 10}           # at least min matches
  - {symbol: "yss.build:build"}                     # importable
  - {command: "python -m yss validate", expect: 0}  # runs only with `yss check --run-commands`
```

Fields annotated in the schemas are checked without any extra authoring: codemap `roots.path`,
`modules.path`, `modules.tests`; design `components.code`. `python -m yss check` prints ok/stale per
doc; `-v` lists passing claims; a stale claim means fix the doc or the code, then bump `updated`.

## References

- Slug fields annotated `x-ref` (`depends_on`, `component`, `module`, `supersedes`, `superseded_by`, `related`) must resolve: `item` (same doc) or `doc/item` (another doc).
- In any text: `[[doc]]`, `[[doc#item]]`, `[[#item]]` (same doc), `[[/doc#item]]` (root doc from inside a collection), `[[doc#item|label]]`. Validated at load, rendered as links. Wrap in backticks to mention the syntax without making a reference.
- `python -m yss refs plan#m3-pilot` lists inbound references before you rename or delete anything.

## Kinds at a glance

- **plan**: `goals[]`, `non_goals[]`, `milestones[] {id, title, status, priority 1-5, target_date, depends_on[], done_when, tasks[] {id, title, status, owner, estimate, depends_on[], done_when, verify, evidence[]}}`, `risks[] {id, title, likelihood, impact, mitigation, status}`, `open_questions[] {id, question, context, options[], owner, status, answer}`. Milestones and tasks use `work_status`; risks `risk_status`; questions `question_status`. **Open work only** - see Archive.
- **design**: `overview` (markdown), `principles[]`, `components[] {id, name, kind, responsibility, inputs[], outputs[], depends_on[], code[] (evidence), status}`, `interfaces[] {id, name, kind, signature, description, component}`, `flows[] {id, title, trigger, steps[] {actor, action, produces}}`, `constraints[]`, `alternatives[]`. Every design list's `status` is `claim_status` (live|decided|open|superseded), components included - a component that exists in the code is `live`, not `active`.
- **codemap**: `roots[] {path (evidence), purpose}`, `modules[] {id, path (evidence), purpose, language, status, exports[], depends_on[], tests[] (evidence)}`, `entrypoints[] {id, name, command, module, description}`, `conventions[] {id, rule, rationale, enforced_by}`. A convention is a repo-level rule on any agent working here; `enforced_by` names the surface that catches a breach (`validate`|`check`|`build`|`scan`|`test`) or `none` when the rule holds by discipline alone.
- **decisions**: `entries[] {id, date, title, status (record_status: proposed|accepted|rejected|superseded), context, decision, consequences[], alternatives[], supersedes, superseded_by}`. Append only; supersede instead of editing.
- **glossary**: `terms[] {id, term, definition (one paragraph), aliases[], see_also[]}`.
- **changelog**: `releases[] {version, date, status, summary, changes[] {type, text, refs[]}}`, newest first. This is the record of shipped work.
- **worksheet**: `intro`, `warn`, `questions[] {id, call, lead, help, kind radio|check|text, required, options[] {value, text, why, recommended, prompt, pros[], cons[]}, applies_to, blocks[] (doc/item ids waiting on the answer), compare}`, `prompt {task, background, steps[], do_not_touch[], flag_only[], rules[]}`. Agents write them; only humans answer them (on the rendered page, which builds a paste-ready instruction with a VERDICTS block).
- **generic**: `shape` and `data` (any object/array). Escape hatch; promote to a real kind when it repeats.

## Vocabularies and limits are data

Defaults (site.yaml `vocabularies`, `limits`) - `lifecycle`, `work_status` (planned|active|blocked|done|dropped),
`record_status`, `risk_status`, `question_status`, `release_status`, `enforcement`; `title` 120, `summary` 300, `line` 240,
`markdown` 2400 characters. A collection overrides them in its `collection.yaml`. Never edit a schema to
change a word list; change the data.

## Archive rule

The plan holds open work only. When a milestone is done and committed, move it to
`docs/_archive/<name>.yaml` (anything starting with `_` is never loaded) in the same commit, add a
changelog line, and drop `depends_on` entries that pointed at it. Old plan revisions never stay in
`docs/plan.yaml`; that is how agents conflate shipped and current work.

## Rules

- Ids: lowercase slugs (`m3-pilot`, `adr-004`), unique within the doc, never renamed (run `yss refs` first if you must).
- Status changes are edits to the field, not new items. Keep `updated` current.
- Plan tasks carry `verify`; add `evidence:` whenever a path or command can prove the task.
- Markdown is allowed in fields documented as markdown; keep other fields plain and within limits.
- Private information: prefer `visibility: private` on the item or `private_notes`; the output scan is a backstop.
- When only the human can decide, write a worksheet doc and a page that renders it; do not guess and do not answer it yourself.
- After editing: `python -m yss validate && python -m yss check`, then `python -m yss query <doc>.<field>` to see what pages will get.

## Adding a new kind

1. Copy an existing schema to `schemas/doc.<kind>.schema.yaml` (site-local; overrides package schemas with the same name) or into a collection's `schemas/`.
2. Set `properties.kind: {const: <kind>}`, describe the body, keep `additionalProperties: false`. Envelope fields and `$defs` are merged in automatically; use `allOf: [{$ref: "#/$defs/item_base"}]` plus `unevaluatedProperties: false` on list items.
3. Use the annotations: `x-vocab: work_status`, `x-limit: line`, `x-ref: item`, `x-evidence: path`.
4. `python -m yss ls kinds` should list it; `python -m yss new doc <kind> <id>` scaffolds it.

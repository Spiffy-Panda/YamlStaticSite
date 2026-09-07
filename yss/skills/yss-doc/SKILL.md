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
mnemonic: PLN              # this doc's handle series; items become citable as PLN.7 from anywhere
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

Any list item may also carry `handle`, `visibility`, `private_notes`, `tags`, `links`, `notes`, `evidence`.

## Handles (citing an item from anywhere)

A doc declares `mnemonic: PLN`; each of its items declares `handle: PLN.7`. That makes the item
citable as `[[PLN.7]]` - through the *same* resolver as `[[plan#m14-audit]]`, to the same item - and
citable from files yss does not read, which `[[doc#item]]` never was. `[[.7]]` is the in-scope form
inside the doc that owns the series. A citation renders with the item's own words as hover text.

- **Declared, never derived.** A handle read out of list position breaks the first time a list is
  sorted; one read out of a slug is a coincidence. Write it on the item.
- **Sub-series.** `<mnemonic>.<tag><n>` splits one mnemonic into several series: a doc whose items
  are questions and decisions writes `VFB.Q5` and `VFB.D4` rather than claiming two mnemonics. The
  tag is up to three letters, and **ordinals count within a series**, so `VFB.D4` leaves no gap at
  4 in `VFB.Q` and each series mints its own next number. `[[.Q5]]` is the in-scope form.
- **Append-only.** Never renumber, never reuse, never rewrite an item to match a later ruling -
  strike it in place and mint a successor. `python -m yss handles` prints each series' mint pointer
  and its gaps. Reuse is a validate error; a **gap** is only checked where a register covers the
  series, because deleting an item outright is legal and leaves one.
- **A series belongs to the site, not to a doc.** `mnemonic:` takes one value or a list, and two
  docs may declare the same one. What is enforced is that a *handle* is unique across the site; a
  declaration only buys a scope for `[[.7]]` (the **first** mnemonic listed) and a prefix the sweep
  trusts. A handle whose prefix no doc anywhere declares is still an error.
- **One namespace, one spelling.** Lookup folds case; the canonical form is uppercase. Mnemonics are
  any short slug.
- **An item with no `id` carries no handle** - there would be no anchor to link to. Its doc keeps a
  mnemonic with an empty series, which is not an oversight.
- **An ordinal identifies an item and dates nothing.** Existing docs were backfilled in file order,
  so a lower number is not older.
- **Bound handles vs mentions.** A handle on a doc item is *bound*: it resolves to exact text and can
  be quoted. A handle written in page prose or a code comment is a *mention*: `yss handles
  --mentions` finds it, but it resolves to a location, never a quote. Only the explicit `[[...]]`
  form links; a bare `PLN.7` stays inert text.
- Archived docs stay in the index at tier `archived`, so a citation to an archived item still
  resolves and reads as spent. Archived means not loaded into a working agent's context, not absent
  from the index.
- A doc that *cites* handles rather than minting them - a register, a generated list - spells the
  field `subject:`, never `handle:`. See `yss-register`.

## Evidence (what makes a doc trustworthy)

```yaml
evidence:
  - {path: yss/cli.py}                              # exists (globs allowed; also tried relative to the collection root)
  - {path: yss/cli.py, contains: "def main("}       # and contains the text
  - {path: yss/cli.py, not_contains: "TODO"}        # and does NOT contain it - proves a removal
  - {path: docs/design.yaml, at: "components[*].status", equals: live}   # anchored: parse the YAML
  - {path: docs/plan.yaml, at: "milestones[*].status", in: [active, blocked]}
  - {path: docs/plan.yaml, at: "open_questions[*].id", absent: true}     # resolves to nothing
  - {glob: "yss/prefabs/*.yaml", min: 10}           # at least min matches
  - {symbol: "yss.build:build"}                     # importable
  - {command: "python -m yss validate", expect: 0}  # runs only with `yss check --run-commands`
```

`contains`/`not_contains` are whole-file substring tests; `at` parses the file as YAML and judges
the *values* at an anchor (`key`, `key.key`, `key[i]`, `key[*]`), with `equals`, `in` or
`absent: true` - and every leaf must satisfy it. **Prefer `at` whenever the target is YAML and the
claim is about a value.** `{contains: "status: open"}` cannot tell an item's status from the
document's own top-level `status:`, so such a claim passes forever; `yss check` now reports it
`warn` ("matched the envelope's own 'status' -- consider at:"). `at`/`not_contains` require `path`;
`equals`/`in`/`absent` require `at`. A non-YAML target, an unparsable file or an anchor that
resolves to nothing is `stale` with a reason, never a crash.

Fields annotated in the schemas are checked without any extra authoring: codemap `roots.path`,
`modules.path`, `modules.tests`; design `components.code`. `python -m yss check` prints ok/stale per
doc; `-v` lists passing claims; a stale claim means fix the doc or the code, then bump `updated`.

## References

- Slug fields annotated `x-ref` (`depends_on`, `component`, `module`, `supersedes`, `superseded_by`, `related`) must resolve: `item` (same doc) or `doc/item` (another doc).
- In any text: `[[doc]]`, `[[doc#item]]`, `[[#item]]` (same doc), `[[/doc#item]]` (root doc from inside a collection), `[[doc#item|label]]`. Validated at load, rendered as links. Wrap in backticks to mention the syntax without making a reference.
- `python -m yss refs plan#m3-pilot` lists inbound references before you rename or delete anything.

## Kinds at a glance

- **plan**: `goals[]`, `non_goals[]`, `milestones[] {id, title, status, priority 1-5, target_date, depends_on[], done_when, tasks[] {id, title, status, owner, estimate, depends_on[], done_when, verify, evidence[]}}`, `risks[] {id, title, likelihood, impact, mitigation, status}`, `open_questions[] {id, question, context, options[], owner, status, answer}`. Milestones and tasks use `work_status`; risks `risk_status`; questions `question_status`. **Open work only** - see Archive.
- **design**: `overview` (markdown), `principles[]`, `components[] {id, name, kind, responsibility, inputs[], outputs[], depends_on[], code[] (evidence), status}`, `interfaces[] {id, name, kind, signature, description, component}`, `flows[] {id, title, trigger, steps[] {actor, action, produces}}`, `constraints[]`, `alternatives[]`. Every design list's `status` is `claim_status` (live|decided|open|superseded), components included - a component that exists in the code is `live`, not `active`. Any design item may also carry `options[] {text, why, pros[], cons[], evidence[], notes}` - the alternatives it is open between; an option's `evidence:` is checked like any other and reported under the enclosing item's id. An item at `status: open` should have them; without the field they degrade to prose no page can table or link. The shape is the envelope's `$defs/option`, a narrowing of the worksheet's; it is **not** on `item_base`, because `doc.plan`'s `open_questions[].options` (strings) and `doc.worksheet`'s (objects requiring `value`) would intersect with it and fail. `alternatives[]` is the closed case - paths not taken, with `rejected_because`.
- **codemap**: `roots[] {path (evidence), purpose}`, `modules[] {id, path (evidence), purpose, language, status, exports[], depends_on[], tests[] (evidence)}`, `entrypoints[] {id, name, command, module, description}`, `conventions[] {id, rule, rationale, enforced_by}`. A convention is a repo-level rule on any agent working here; `enforced_by` names the surface that catches a breach (`validate`|`check`|`build`|`scan`|`test`) or `none` when the rule holds by discipline alone.
  Each `exports[].name` is evidence too: it is resolved by parsing the file at `modules[].path`, so a renamed export is a stale claim rather than a dead link. **Python and Rust are parsed** (`yss/langs/`); a module in any other language is still valid and simply carries no line ranges. A `path` may be a file or a directory - a directory prefixes each file's names with its stem (`buildinfo.collect`, `ballistics::GRAVITY`) and leaves `__init__.py`, `mod.rs`, `lib.rs` and `main.rs` bare. In a collection, paths are relative to the collection folder.
  Rust names: `fn`/`struct`/`enum`/`trait`/`type`/`mod`/`const`/`static` at the top level, `Type::method` for an `impl` or `trait` member (a trait impl indexes under the **type**), `Enum::Variant`, `mod_name::item` one level into an inline `mod`, and a `use ... as` rename. Not indexed: struct fields, anything inside a function body, anything a macro generates. Write `Type::method`; `Type.method` resolves to the same lines but reads as another language's spelling.
- **decisions**: `entries[] {id, date, title, status (record_status: proposed|accepted|rejected|superseded), context, decision, consequences[], alternatives[], supersedes, superseded_by}`. Append only; supersede instead of editing.
- **glossary**: `terms[] {id, term, definition (one paragraph), aliases[], see_also[]}`.
- **changelog**: `releases[] {version, date, status, summary, changes[] {type, text, refs[]}}`, newest first. This is the record of shipped work.
- **worksheet**: `intro`, `warn`, `questions[] {id, call, lead, help, kind radio|check|text, required, options[] {value, text, why, recommended, prompt, pros[], cons[]}, applies_to, blocks[] (doc/item ids waiting on the answer), compare}`, `prompt {task, background, steps[], do_not_touch[], flag_only[], rules[]}`. Agents write them; only humans answer them (on the rendered page, which builds a paste-ready instruction with a VERDICTS block).
- **worksheet, once it is answered**: a question's `resolution {summary, chose[] (must name option `value`s - validated), answered (kind: text only), applied, by, recorded_in[]}` turns it into a record. A sheet may then archive itself: `visibility: on_resolution` at the top of the doc is derived, not authored - private while any question that requires an answer lacks a `resolution`, public the moment the last one lands. What publishes is a **projection**, not the doc you wrote: `resolution.summary`, `agent_notes` and `private_notes` are elided, and a `kind: text` answer publishes only as the `answered` marker (`<filled>` / `<empty>`), so record the marker when you apply a text verdict or validation fails. Write one sheet per deliberation, not one accumulating sheet: a sheet with a live question in it can never flip.
- **register**: `covers[]`, `entries[] {subject, says, state (register_state), lives_in (evidence), pairs[], note}`, `pairs[] {id, kind hard|soft, state (prose; only the first word is read), a/b {subject, quote}, resolves}`, `dismissed[] {pair, why}`, `prompt`. A **view over handles that live in other files**; it answers nothing and carries no `resolution:`. Every pair quotes both sides verbatim or it does not go in. **Never archived** - the settled rows are what make the live ones legible. Load `yss-register` before touching one.
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

*Never loaded* means never loaded **into a working agent's context** - not absent from every index.
An archived item keeps its `handle:` and stays resolvable at tier `archived`, so a page that cites it
still resolves and reads as spent rather than dangling. And the rule is about `doc.plan`: a
`doc.register` is a view over handles and is **never archived**, because its settled rows are what
make its live ones legible.

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

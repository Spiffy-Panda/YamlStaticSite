---
name: yss-register
description: Run a yss contradiction register and its generated live-rules list - handles and mnemonics, the register doc and page, the verdict-batch maintenance loop, retirement, the mention sweep and the failure modes the generator refuses on. Use when asked about a contradiction register, live rules, "which rules still bind", retired or spent handles, a handle series, recording or applying a verdict batch, adding a handle or a contradiction pair, or context poisoning from stale rules.
---

# The contradiction register and the live-rules list

Two instruments over one thing: **handles**. They answer different questions and must never be
swapped.

| Instrument | Question it answers | Who reads it |
|---|---|---|
| Contradiction register — `<collection>/docs/register.yaml` | *Where does this collection disagree with itself, and what did each handle ever say?* | an agent auditing history |
| Live-rules list — the generated `docs/live-rules.yaml` and its page | *What binds me right now?* | **an agent about to do work** |

**A working agent loads the live-rules list, not the register.** The register keeps every handle,
retired ones included, **with their original confident wording standing** beside the rules that
survived. Reading it to learn the rules is how an agent ends up obeying something that was struck.
Say so in `collection.yaml`'s `agent_notes` in as many words.

## Handles, first

Everything here is a view over handles, so start with `yss-doc`'s handle section if you have not.
In one paragraph: a doc declares `mnemonic: XYZ`, each of its items declares `handle: XYZ.3`, and
the site-wide index resolves `XYZ.3` to that item. A series belongs to the **site**: `mnemonic:`
takes one value or a list, two docs may declare the same one, and what is enforced is that a handle
is unique across the site - which is why a register's `covers:` names series rather than docs. Handles are **append-only** — never renumbered,
never reused, struck in place rather than rewritten. The ordinal identifies the item and says
nothing about when it was written.

**Sub-series.** `XYZ.Q5` is the same mnemonic with a tag: up to three letters splitting one
mnemonic into several series, for a doc whose items fall into kinds (questions, decisions, beats).
Ordinals count **within a series**, so `XYZ.D4` leaves no gap at 4 in `XYZ.Q`, each series mints its
own next number, and the gap check runs per series. A register's `series[].prefix` may name a whole
mnemonic (`XYZ`, covering every sub-series) or one series key (`XYZ.Q`).

Examples below use the fictional prefix `XYZ`. Do not use a *live* prefix in documentation: the
mention sweep reads markdown, so an example handle in a real series is reported as a handle with no
register row - which is the sweep working, and noise you have to explain away.

```bash
python -m yss handles                    # every mnemonic, its size, its mint pointer, its gaps
python -m yss handles XYZ.3              # one handle: text, tier, home file
python -m yss handles --series XYZ       # a whole mnemonic, or one series key like XYZ.Q
python -m yss handles --mentions         # handles written outside a doc item; exit 1 if any is unregistered
```

**Bound handles and mentions** is the distinction the register stands on:

- A **bound handle** lives on a doc item. It resolves to exact text, so it can be quoted.
- A **mention** is a handle written in page prose, a Rust comment, a commit message. The sweep finds
  it and the register must have a row for it, but it resolves to a *location*, never a quote — a
  mention has no boundary, so there is no honest answer to where the item's text stops.

**A pair may only name bound handles.** That is not a technicality: a pair whose second half is a
paraphrase is a pair an agent invented. When a mention needs to be contradictable, it becomes a doc
item that carries a handle first.

## What a register is

A **view over handles that live in other files**, with the pairs that contradict each other quoted
on both sides. It has no authority over anything it lists. It **answers nothing, resolves nothing,
and carries no `resolution:` field.** A verdict is the human's and it lands where the handle lives —
struck in place, or corrected in its own file. The register reflects it second.

### The quoting rule — the load-bearing one

> Every claimed contradiction quotes both sides verbatim, from the item the handle names.
> **A contradiction that cannot be quoted is one an agent invented, and inventing them is worse
> than missing them.** If you cannot produce the conflicting text of both items, the pair does not
> go in.

Candidates that fail are not deleted silently — they go in `dismissed` with the reasoning, so a
later agent does not re-propose them.

### `kind: hard` vs `kind: soft`

- **`hard`** — both items cannot be true or acted on at once.
- **`soft`** — tension, drift, or an item a later ruling has quietly made misleading without
  formally superseding it.

## Setting one up

**1. Configure the collection.** Everything unique to one collection goes in its own
`collection.yaml` (site.yaml for the root collection, which is the root's config file):

```yaml
register:
  doc: register            # docs/register.yaml
  out: live-rules          # the generated docs/live-rules.yaml
  series:
    - prefix: XYZ
      label: What this series is about      # the first line of a citation tooltip
      strike_forms: ["~~{handle}", "{handle} - RETIRED"]
      unminted:
        - {handle: XYZ.9, why: named by the design before the item exists}
  sweep_ext: [".yaml", ".md", ".rs"]       # optional; defaults to the built-in list
```

Two traps, both learned the hard way:

- **A collection may own several series.** One collection had one prefix; another had six. Gap
  checks, the mention sweep and the retired list are all per series.
- **A strike does not mean the same thing everywhere.** In one collection a strike marks a retired
  handle; in another a correction strikes *superseded text inside a claim that is still live*.
  Pointing the first collection's patterns at the second read four live claims as retirements.
  `strike_forms` is per series and every pattern anchors on the handle.

**2. Write the register** — `kind: register`, `visibility: private`. `python -m yss schema
doc.register --yaml` prints the exact fields.

```yaml
kind: register
id: register
title: <collection> register
visibility: private          # explicitly, never inherited - see below
updated: 2026-09-07
covers: [XYZ]
entries:
  - subject: XYZ.3           # `subject`, not `handle` - see the note below
    state: met
    lives_in: docs/plan.yaml
    pairs: [public-before-the-verdict]
    note: Whose verdict it was, and what it does NOT license.
pairs:
  - id: public-before-the-verdict
    kind: soft
    state: open 2026-09-07 - an agent's reading, reversible by Panda
    a: {subject: XYZ.3, quote: "verbatim from the item XYZ.3 names"}
    b: {subject: XYZ.4, quote: "verbatim from the item XYZ.4 names"}
    resolves: What it would take to settle this.
dismissed:
  - {pair: the candidate, why: the reasoning, so it is not re-proposed}
prompt: {task: …, background: …, rules: […]}
```

`covers:` is the register's own statement of what it is responsible for and the collection's
`register.series` is what the generator acts on. Both exist so the doc is self-describing, and a
disagreement between them is a refusal rather than a silent resolution - either half could be the
wrong one.

> **`subject:`, never `handle:`.** `handle:` is how an item *declares* its own handle. A register
> declares nothing — it cites. Spelling both the same would make a register row look like a
> handle-minting item to the index, and would read as one to an agent editing at speed.

**3. Write the page**, private, binding the built-in prefab:

```yaml
sections:
  - id: register
    type: prefab
    prefab: contradiction-register
    args:
      title: {from: register.title}
      intro: {from: register.intro}
      entries: {from: register.entries}
      pairs: {from: register.pairs}
      dismissed: {from: register.dismissed}
      prompt: {from: register.prompt}
```

Contradictions render first — they are why the page exists — then every handle with two controls,
*mark for discard / retire* and a comment box, then **Build the instruction**. The generated text
contains only the rows the human touched; an untouched row contributes nothing, not even a
placeholder. That button is what makes a register an **instruction builder** rather than a list.

**4. Generate**, then add the page for the output:

```bash
python -m yss live-rules <collection>
```

> **Bootstrap order.** A register that links to `[[live-rules]]` cannot validate before that doc
> exists. Write the register without the forward link, generate once, then add the link and
> regenerate.

## The state vocabulary

`entries[].state` is a **closed set** — `site.yaml`'s `register_state` vocabulary, and the
generator refuses anything outside it, because an unknown state has no section to be emitted into
and the rule would silently vanish.

| state | Meaning | Reaches the live-rules list as |
|---|---|---|
| `live` | still binds | **Rules in force** |
| `met` | a stated deliverable that was delivered | Settled |
| `answered` | a question that got a verdict, which stands | Settled |
| `recorded` | a fact about the build that was written down, not a rule | Settled |
| `retired` | struck; binds nothing | a **bare number**, no text |

"Settled" is not "re-openable": the generated list says these were decided and the decision stands.

`pairs[].state` is **not** a closed set — it is a prose line, because what settled a contradiction
is rarely a word. Only its first word is machine-read: a state beginning `closed` is closed,
anything else is open.

## Retirement

Retiring a handle **strikes it in place**, in the file it lives in, keeping its number and its
original wording.

- Never renumber, never reuse a retired number, never rewrite an item to match a later ruling.
- A successor is a **new** handle with the next number, noting which handle it succeeds and why.
- **Retiring a rule removes it; it does not supply the opposite.** Retiring a rule that forbade a
  design document is not a commission to write one; retiring a rule that set a scope does not
  license inventing inside that scope.
- Where the human gave no reason, record that no reason was given. **Never infer one.**
- Mint a successor only where an agent's behaviour would otherwise be wrong. Where the human said
  to discard something *without* replacing it, no successor may be minted — one would have to
  contain the guess that was forbidden.

**A handle can be retired unanswered, and that settles nothing.** A question retired together with
the handles it was arbitrating is neither ratified nor rejected. Say so precisely, in every place
that mentions it, so its closure is never read as confirmation.

## Never archived

`yss-doc`'s archive rule is a rule about `doc.plan`: the plan holds open work, and a done milestone
moves to `docs/_archive/`. It exists because a plan that keeps finished work stops distinguishing
shipped from current.

**A register has the opposite property.** It is a view over handles; a handle does not stop existing
when its question is answered, and a contradiction is usually between one live item and one settled
one — archiving the settled rows would delete exactly the rows that make the live ones legible.

(Archiving does not break a citation either. Archived docs stay in the handle index at tier
`archived`: *archived* means not loaded into a working agent's context, not absent from the index,
so `[[XYZ.3]]` still resolves and reads as spent rather than missing.)

## `visibility: private`

The register doc **and** its page carry it, explicitly set, never inherited — sibling pages in the
same folder are usually public. Do not flip it.

A doc's full source is exported to `dist/<target>/data/docs/<collection>/<id>.json` whether or not a
page binds it, and `dist/public/` is uploaded wholesale. **The key is the only guard**, and binding
a private doc from a public page is a build error.

A public page *may* still cite a private handle: in the public build the doc is not there, so the
citation renders as inert text and no wording crosses the line. That is deliberate, and it puts the
responsibility on the surrounding prose. The related trap: a correction written onto a *public* page
once named the private files by name, and `yss scan` did not catch it — it looks for forbidden
identity strings, and those were ordinary filenames. Grep the built public tree before pushing.

## The live-rules list, and the problem it solves

> "a script should run that creates list of just live rules ... its main purpose is to stop future
> agents from having to [read] this file and get there context poison'd."

A register of forty handles, a third of them retired **with their original confident wording
preserved**, hands a working agent a large body of rules that no longer bind, stated as though they
did. The register is right for auditing history and wrong to hand a working agent.

What the generated list carries:

- **Rules in force** and **Settled** — the row's `says`, or, when the row leaves it out, the bound
  item's own words. The generator keeps no second copy of any rule, so it cannot drift into
  disagreeing with its source.
- **Open tensions** — only pairs that are open *and* whose named subjects are all non-retired.
- **Retired handles — numbers only.** No text. Listed so a number reads as *spent* rather than
  missing, without teaching a reader what it said.
- A reference to a retired handle inside a *live* item's prose is tagged `*[retired]*`, unless the
  surrounding text already says so. That is what stops a live note asserting that a retired handle
  still binds — and the note is not ours to rewrite.

**Committed, not gitignored**, for the obvious reason: a file you must run a generator to read is a
file a fresh agent will not read. **Never hand-edit it**; every edit is lost on the next run.

Deterministic: nothing reads the clock. `updated:` is copied from the register and provenance
carries a sha256 digest, so a no-op run is byte-identical and `--check` still means something the
next day.

## The generator fails rather than drops

A rule that vanishes to a missed pattern is indistinguishable, to the agent reading the output,
from a rule that does not exist. So every one of these exits non-zero with the reason instead:

1. The register has no `data`/`updated`/`entries` — its shape changed.
2. A row missing `subject` or `state`; a malformed handle; two rows sharing a number.
3. A `state` outside the closed vocabulary.
4. A row whose `says:` is present but empty, or which has neither a `says:` nor a bound item whose
   words could be quoted — it refuses to emit a rule with no text.
5. A row with no `lives_in:` and no bound item to take one from, or a `lives_in:` that does not
   exist.
6. A handle **not mentioned in its own home file** — the register has drifted.
7. `retired` on the register but **not struck in place** in the home file (record the verdict where
   the handle lives *first*), or the reverse — struck in place but not `retired`. It will not pick
   which of the two is wrong.
8. A **gap in the series** — append-only means a gap is a missing row, not a free number.
9. A handle written **anywhere in the collection** with no row (declare it `unminted`, with the
   reason it is named but unspent, when that is genuinely the case).
10. A pair naming a handle with no row; a pair marked `closed` with no retired subject; a pair left
    open that names a retired subject.
11. A pair missing a verbatim quote on either side, or naming a **mention** rather than a bound
    handle.

Strike marks are matched exactly, never fuzzily, and always anchored on the handle, so a `~~`
merely appearing earlier in a file cannot read as a retirement.

`--check` regenerates in memory, writes nothing, and exits 1 if the committed output is stale. It
ships as a `command:` evidence claim on the generated doc, so `yss check --run-commands` runs it.
**Nothing runs it automatically** — a register edited without a re-run leaves the output quietly
stale until someone checks. Detectable, not prevented; that is why step 6 of the loop below exists.

## The maintenance loop

The human marks rows on the rendered register page and presses **Build the instruction**; the
pasted text is the verdict batch. Then:

1. **Record each verdict where the handle lives, first.** Strike a retired handle in place in its
   own file, with the reason beside it; append a correcting sentence to an item that was commented
   on but not retired. Never edit an item's original claim, options, resolution or number.
2. **Mint successors** only where an agent would otherwise behave wrongly, starting from
   `python -m yss handles`' mint pointer. Note on each which handle it succeeds and why.
3. **Update the register rows second** — `state`, and a `note` saying whose verdict it was, what it
   does *not* license, and "no reason given" where none was.
4. **Update the pairs.** A pair whose subject was retired gets its `state` changed and its
   `resolves` rewritten to say what actually ended it. **Both quotes stay standing**, and no
   `resolution:` field is ever added. A dismissed pair moves to `dismissed` with the why.
5. **Bump the register's `updated:`** — the generated file copies it.
6. **Re-run the generator**, then validate, then check.
7. Commit the register, the files the handles live in, and the generated output **in the same
   commit**.

```bash
python -m yss live-rules <collection>
```

```bash
python -m yss validate
```

```bash
python -m yss check --run-commands
```

## Things that bite

- Recording a verdict on the register alone. The register never carries a verdict alone; the
  generator enforces this for retirements by refusing when the home file is not struck.
- Editing an item to match a later ruling. Append-only: strike in place, mint a successor.
- Reading a retirement as its opposite, or reading a handle retired *unanswered* as answered.
- Adding a pair you cannot quote both halves of, or one naming a mention.
- Handing a working agent the register. Hand it the live-rules list.
- Hand-editing the generated file, or editing the register and forgetting to re-run — nothing runs
  the generator for you.
- Flipping `visibility: private` on either doc or either page.
- Writing a handle anywhere in the collection without adding its register row; the next run fails.
- Spelling a register row's `subject:` as `handle:` — that field declares, it does not cite.

# slot → site — 0-based `site_index`, frozen `site_name`, index-or-name CLI

**Status:** design, converged (2026-07-01). Shaped in discussion; **not implemented.**
This is Task #1 — the **last 0.3.0 blocker** — and 0.3.0 is intended to be the final large
schema wipe, so the base/naming decisions here are baked in, not migrated later.

**Supersedes** the earlier in-discussion lean toward `site_number`, 1-based. That lean rested
on "STDF `SITE_NUM` is 1-based / site 0 = all sites," which did **not** survive a prior-art
check (see §Prior art). The evidence flipped the decision to **0-based, end-to-end**.

**Relates to** `step-vector-grain-reshape.md` (the sibling reshape; shares the "at-rest rows
freeze what actually ran, never join back to mutable config" principle) and Task #4 (fixture as
a first-class entity) and #8 (multi-site launch parity).

---

## One breath

A **site** is a parallel-UUT position in a multi-site test (STDF `SITE_NUM`, NI TestStand
"test socket"). We drop the made-up word `slot` for the industry-standard `site`. A site's
machine identity is a **0-based index** (`site_index`); its human handle is an **optional
frozen name** (`site_name`). Config defines the sites as an ordered list; the *number falls out
of position* and is never authored. The CLI addresses sites by index **or** name.

---

## The naming law (schema-wide invariant this establishes)

An integer field falls into exactly one of two shapes. The **name carries the base**, so a
reviewer (or a linter) can check it:

| shape | fields | base | what `0` means |
|---|---|---|---|
| **position** — `*_index` | `step_index`, `vector_index`, `vector_outer_index`, **`site_index`** | **0-based** | *first* |
| **quantity** — count noun | `retry`, `*_count` | starts at 0 | *none* |

- A field is an `*_index` **iff it addresses a position in an enumerable collection** (there are
  N sites / N vectors / N steps — you can point at "the 3rd one"). Position → 0-based index.
- `retry` is **not** an index: there is no collection of "retries" to point into. It is an
  open-ended **tally** of one event recurring ("how many times did we redo this"). A magnitude,
  not an address. It stays a bare count noun — never `retry_index`.
- The tell: **for an index `0` = *first*; for a count `0` = *none*.** Both start at zero for
  opposite reasons.

**Corollary — no integer `*_number`.** With `slot → site_index`, nothing numeric is a
`*_number` anymore. `*_number` / `*_id` are reserved for **external string identifiers**
(`uut_serial_number`, `part_number`, `station_id`). Every integer ordinal in the schema is now
either a 0-based position or a count. No integer ever carries a domain base again.

---

## The base decision — 0-based, everywhere, no translation (decision "A")

`site_index` is **0-based in events, at-rest parquet, query filters/results, STDF export, the
operator UI, and the CLI.** One base, top to bottom, **no edge `+1`.**

We explicitly did **not** take the alternative ("store 0-based, display 1-based to operators"),
because a 1-based *display* and a 0-based *CLI* disagree by one: a report printing "Site 1
FAILED" and an operator typing `--site 1` would target different units. Display and addressing
must share a base or operators mis-target on the floor. So operators see `site_index` too; the
**`site_name`** ("left"/"right") is the human-friendliness, not a re-based number.

STDF: `SITE_NUM = site_index`, emitted directly. STDF is tester-specific on base and real-world
data is commonly 0-based, so this is conformant with zero translation.

---

## Event + at-rest shape — emitted on events, projected to rows

Events are the source; at-rest parquet + queries are **derived** (accumulator projection), so
`site_index` / `site_name` are stamped **on the events at emit time** — never added at the
parquet layer. The freeze happens at emission.

**`slot_id` is removed, not renamed.** Today `slot_id` (the string `"slot_1"` / `"default"`)
is the *primary identity* — the `slots` dict key, the `_LITMUS_SLOT_ID` env, the reservation /
routing key, the `SlotResult` key, the `--uut-serials slot_1=` key — and `slot_index` is
*derived* from it (`slot_ids.index(slot_id)`). The reshape **inverts** that: the integer
**position becomes the identity** and the synthetic string identifier disappears entirely.

- `site_index` (int) takes over **every** role `slot_id` held as a key.
- `site_name` is a **new, optional** field — a real human label ("left"), usually `null` — **not**
  `slot_id` renamed. There is no `site_id`; the `"slot_1"`/`"default"` synthetic string is gone
  (single-UUT is simply `site_index 0`).

**Where it's emitted:**

- **`RunStarted`** — the per-site-worker run carries **`site_index`** (was `slot_index`) +
  **`site_name`** (new). This is the freeze point: the worker reads its `site_index` from
  `_LITMUS_SITE_INDEX` and resolves `site_name` from the active fixture's site at that index.
  Every step / vector / measurement row is attributed to a site by **projection through its
  `run_id` → `RunStarted`** — the accumulator does not need site fields on each measurement
  event.
- **`SessionStarted`** — `slot_count` → `site_count`, **plus** the full roster
  (`site_index → site_name`) for the orchestrator's view.
- **`SlotStarted` / `SlotCompleted`** → **`SiteStarted` / `SiteCompleted`**, keyed by
  `site_index` (int) instead of `slot_id` (str); carry `site_name`.

**Resulting two-field pair, denormalized onto rows by the projection:**

| field | type | role | base / null |
|---|---|---|---|
| `site_index` | `int` | machine key — the position; what queries and joins key on | 0-based, always present |
| `site_name` | `str \| None` | operator label, **frozen at run time** | `null` when the site was unnamed |

- **Why freeze `site_name`** (not join back to fixture config on read): config is mutable YAML,
  the event/row is immutable. Rename site 1 "left" → "top" next month and every historical
  `site_index=1` row silently changes meaning unless the name was frozen at emission. Same
  treatment as `uut_part_number` / `station_id` — the record is *what ran*.
- **Denormalized onto the rows** (the accumulator projects `RunStarted`'s pair onto each
  derived row, like `uut_part_number`), so at-rest rows are self-describing and reads need no
  join. Low-cardinality, constant per site within a run → RLE-packs fine.
- **Null `site_name`** → `site_index` is the display identity (the "operator sees Site 0" case).

---

## Config shape — ordered `sites` list, number derived from position

Replaces `FixtureConfig.slots: dict[str, FixtureSlot]` with an **ordered list**. `site_index`
is the list position — **never authored** (a field the author can only fill one correct way is
ceremony that invites the bug it pretends to prevent). Config is structurally **dense**
(1..N contiguous, no gaps expressible); only a *launch* can be sparse (§CLI).

```yaml
id: quad_bench
part_id: buck_3v3
station_types: [bench]
sites:
  - name: left            # optional operator handle; omit → the site is just "site 0"
    connections:
      vout_measure: { name: vout_measure, uut_pin: TP_VOUT, instrument: dmm, instrument_channel: '1' }
  - name: right
    connections:
      vout_measure: { name: vout_measure, uut_pin: TP_VOUT, instrument: dmm, instrument_channel: '2' }
```

- Single-UUT fixtures keep bare `connections:` (implicitly `site_index 0`); `sites:` is the
  multi-UUT form; `connections` XOR `sites` mutual-exclusion carries over from today's
  `connections` XOR `slots`.
- **Validator rejects a bare-integer `name`** (a site can't be named `"3"`) — fails at fixture
  **config load**, not at runtime. This kills the corner case *"someone names `site_index 0`
  the name `"1"`"*: without the ban, `--site 1` is ambiguous (index 1 = the second site, or the
  site *named* "1" = the first?), and worse, the name silently shadows a real index. Fail-fast
  with a clear message ("site name '1' is numeric; names must be non-numeric so `--site` /
  `--uut-serials` resolve index-vs-name unambiguously") beats a shadowed, unreachable name.
  Only **pure** integers are banned — `"S1"`, `"pos1"`, `"bankA"` int-parse-fail and resolve
  cleanly as names. A 1-based silkscreen label is a *display* concern, which decision A already
  declines; use a non-numeric name for it.
  - **Deferred (revisit if demand appears):** the ban is provisional — it exists only because
    `--site`/`--uut-serials` resolve index-vs-name by int-parsing the token. If a real need for
    numeric site names shows up, we can lift it later behind an **explicit selector** that
    removes the ambiguity (e.g. `--site name:1` vs `--site index:0`, or quoting), without
    changing any stored shape. Ban now, keep the door open.
- Per-site `uut_resource` stays available on each site object, as today's `FixtureSlot` has it.

---

## CLI — `--site` and `--uut-serials`, both take index or name

`--slot` → `--site`. `_LITMUS_SLOT_ID` / `_LITMUS_SLOT_INDEX` → `_LITMUS_SITE_INDEX` (0-based).

**Resolution rule (both flags):** int-parse the token first → `site_index`; else → match
`site_name`. Unambiguous because names can't be bare integers.

- **`--site <index|name>`** — target one site (single-process worker):
  - `--site 0` → `site_index=0` (first site)
  - `--site left` → resolves name → its `site_index`

- **`--uut-serials`** — assign serials to sites. Two auto-detected forms (as today):
  - **Positional (dense):** `--uut-serials SN001,SN002,SN003` → position **is** `site_index`:
    `0=SN001, 1=SN002, 2=SN003`. Count must equal the site count — positional means "all of
    them, in order." First element → `site_index 0` (0-based by position, no off-by-one).
  - **Keyed (sparse OK):** any token containing `=` switches to keyed mode; keys are index or
    name; **gaps allowed**:
    - `--uut-serials 0=SN001,2=SN003` → loads sites 0 and 2; sites 1,3 idle this run.
    - `--uut-serials left=SN001,right=SN003` → same, by name.
  - **Strict: positional OR keyed, never mixed** (locked). A string is all-positional or
    all-keyed — `SN001,2=SN003` is an error. Detection: any `=` present → all-keyed mode.
    "All of them, in order" and "these specific ones" are distinct intents; half-and-half has
    no unambiguous meaning. (Matches today's grammar; now an explicit rule, not an accident.)

Sparse **launch** is how STDF-sparse `SITE_NUM` output arises (e.g. only sites 0 and 2 loaded →
emitted SITE_NUM `0, 2`): **dense definition, sparse population** — the config can't express a
gap, the launch can.

---

## Blast radius (rename surface — for execution, not yet done)

- **Code:** `execution/slots.py`, `execution/slot_runner.py` (`SlotRunner`,
  `run_multi_slot_session`, `SlotResult`), `execution/uut_provider.py` (`parse_serials`,
  slot_ids → site ordering), `_state.py` active-slot getter.
- **Models:** `FixtureConfig.slots` → `sites: list[FixtureSite]`; `FixtureSlot` → `FixtureSite`
  (+ optional `name`); `slot_count`/`is_multi_slot` → `site_count`/`is_multi_site`.
- **Events:** `SlotStarted`/`SlotCompleted` → `SiteStarted`/`SiteCompleted`; `slot_index` →
  `site_index`; **`slot_id` removed** (its key role → `site_index` int); **add** new optional
  `site_name`; `SessionStarted` roster. `SlotResult` keyed by `slot_id` string →
  `SiteResult` keyed by `site_index` int.
- **At-rest schema:** `schemas.py` `slot`/`slot_index` column → `site_index` + `site_name`
  (denormalized onto run/measurement rows).
- **Queries:** `runs_query.py`, `steps_query.py`, `measurements_query.py` slot filters/columns.
- **Wire/env:** `_LITMUS_SLOT_ID`/`_LITMUS_SLOT_INDEX`/`_LITMUS_SLOT_COUNT` →
  `_LITMUS_SITE_INDEX`/`_LITMUS_SITE_COUNT`; `--slot`, `--uut-serials` help text.
- **UI:** `execution_gantt.py` stack naming, any slot columns/labels → site.
- **Docs:** reference docs (event-types, models, cli, configuration) regenerate;
  fixtures/multi-site user docs.

Carried by the `schema_version` scheme like the grain reshape, so it's reversible if wrong.

---

## Prior art (recorded so the base is not re-litigated)

Every readable source — internal and industry — puts the site *engine/data* base at **0**; the
only 1-based usage found is an optional **report display** preference, which decision A declines.

- **NI TestStand Semiconductor Module** — `Parameters.TestSocket.Index` is **0-based by
  default** (first site = Socket 0). NI notes "some users prefer 1-indexed *reports*" — i.e. 0
  is the engine base, 1 is a display choice. (TestStand is a named Litmus migration path.)
- **STDF** — `SITE_NUM` is `U*1`, **tester-specific** (spec mandates no base); canonical
  multi-site example data shows `SITE_NUM = [0, 1]`. The "all" sentinel is `HEAD_NUM = 255`,
  not "site 0."
- **Litmus internal** — `slot_index = slot_ids.index(slot_id)` (0-based), `vector_index`,
  `step_index` all 0-based. No exceptions.

Sources: NI TSM multisite docs; pySTDF `SITE_NUM = [0,1]` example; STDF V4 spec.

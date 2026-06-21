# Query by role + name — drop the fused `out_<name>` columns

**Status:** **COMPLETE 2026-06-21** (Phases 1–5 landed; final suite 2143 passed / 0 failed,
ruff clean). Deferred follow-ups tracked separately: run_store un-fuse + its 4 straddling docs
pages (#6), channels `data_type`→`value_type` (#4), resource `*Query` unify (#3),
`units`→`unit` sweep (#7, Pyright-only), operator-UI screenshot regen (#2). Minor carry-overs:
`concepts/data/index.md` summary line + "three verbs" page framing; stale `harness.py:251`
`in_*` comment. This is the execution diary (design
contract + progress log) for the v0.2.0 measurement-query redesign.
**Branch:** `feat/0.2.0-query-by-side-name` (off `feat/0.2.0-data-improvements` @ `586b8a3`).
The branch name predates the vocabulary decision below — the concept is **role**, not
"side"; the branch keeps its name to avoid a mid-flight rename.
**Window:** 0.2.0 clean break — no users, no back-compat shim, one representation.
**Related:** [`measurement-storage-eav.md`](measurement-storage-eav.md) (the EAV at-rest
store this queries), [`runs-execution-model.md`](runs-execution-model.md) (parent model).

---

## 1. What this is about, in plain terms

When a test runs, it records three kinds of named value on each measurement:

- **inputs** — what you *set* (the stimulus). Written by `configure("vin", 5.0)`.
- **outputs** — what you *read back* (the response). Written by `observe("v_rail", 3.3)`.
- **the measurement** — the *judged result* with limits and a pass/fail outcome.
  Written by `verify("v_rail", 3.31, limit=...)` / `measure(...)`.

To analyze that data afterwards you use the **query API** — the `MeasurementsQuery`
client (`src/litmus/analysis/measurements_query.py`). It's what the operator UI, the
`litmus metrics` CLI, and the `litmus_metrics` MCP tool all read through.

**Today's problem:** inputs and outputs are stored twice — once in a clean typed table
keyed by `(role, name)`, and *again* as flattened columns named `in_vin`, `out_v_rail`,
`custom_*`. Those flattened names are also the *query vocabulary*: you ask for
`out_v_rail`. That fused `role+name` string is the defect — it re-creates the prefix
collisions and column explosion the typed table was built to remove. **The observed name
should be the key; the role should differentiate.** This redesign drops the fused columns
and makes the typed `(role, name)` table the one source.

### Glossary (terms I'll use throughout)

- **role** — which of the three kinds a value is: `input`, `output`, or `measurement`.
- **parametric viewer** — the `/explore` page ("Measurements" in the nav). It plots one
  recorded field against another **across many runs** — e.g. measured `v_rail` (Y) versus
  swept `vin` (X) — as a scatter or line. "Parametric" is standard ATE vocabulary (in
  STDF, a measured value is a *Parametric* Test Record). The query method behind that page
  is `parametric()`; it returns the `(x, y)` point pairs to plot.
- **histogram** — a *distribution* of one field: bucket its values into bins and count how
  many fall in each. A different question from "Y vs X" (see §4).
- **aggregate methods** — the canned analytics: `cpk`, `pareto`, `yield_summary`, `trend`,
  `retest`, `time_loss`. They group and summarize; they don't plot point pairs.

---

## 2. Vocabulary — one word, end to end: `role` (`FieldRole`)

Today there are **three** competing words for this one axis:

| Layer | Word | Values |
|---|---|---|
| At-rest nested lane columns (lists) | — | `inputs` / `outputs` / `custom` |
| EAV column + flat prefixes | `side` | `in` / `out` / `custom` |
| Existing codebase enums | `role` | `PinRole`, `TerminalRole` (the `…Role` convention) |

**Decision:** standardize on **`role`** everywhere — storage, query, UI, docs. It matches
the codebase's own `…Role` enum convention, reads naturally ("the role this field plays"),
and replaces "side" (rejected as opaque jargon).

- **`FieldRole(StrEnum)`** — the cross-cutting enum. Values: `INPUT = "input"`,
  `OUTPUT = "output"`, `MEASUREMENT = "measurement"` (UPPERCASE member / lowercase value,
  matching `PinRole.SIGNAL = "signal"`). Full singular words, not `in`/`out`.
- It's an **open** enum: a future role is additive (one value + a writer that produces it).
- The EAV column is literally named `role`; the Python type is `FieldRole`.

### Naming principle — why `out_*` changed but `run_outcome` stays

The presented field name is a **redirect/curation layer** over stable storage (the EAV +
fixed columns), so any name can map to any presented form, additively. The default redirect:

> **Structured refs (`FieldRef`) for open, user-named namespaces; flat names for closed,
> system-defined schemas.**

`out_v_rail` *had* to change because it fused a system prefix onto a **user-chosen name in
an open namespace** — the user typed `observe("v_rail")` but had to query `out_v_rail` (a
name they'd never guess), and the space grows unbounded (column explosion). `run_outcome`
is the opposite: a **closed, system-defined** field — nobody named "outcome," cardinality is
fixed, no user identifier is mangled. So it stays flat for 0.2.0. (`measurement_name` is
already correct — it holds the user's name as a *value*, not baked into a column name.)

Destructuring system fields to a dotted model (`run.outcome`, `step.outcome`) is **optional
polish** — the cross-resource grammar northstar (§9) — not the forced produce/consume fix
`out_*` needed. The freedom to redirect should serve model alignment (match how users
produce/consume), not spawn arbitrary aliases.

## 3. `custom` is dropped

Confirmed by reading the whole producer path: **nothing populates `custom`.**
`configure()` → inputs; `observe()`/`verify()`/`measure()` → outputs/measurement; that's
every verb. (`namespace=` is dotted-prefix sugar on the *name*, not a role router.)
`custom` exists as a field, a lane, an EAV value, a `custom_` prefix, and a read-back — with
zero writers anywhere in the harness, verbs, examples, or tests. (Distinct from run-level
`RunContext.custom_metadata`, which *is* used.)

**Decision:** drop `custom` in the clean-break window — the lane, the `custom_` prefix, the
read-back. `FieldRole` is `input | output | measurement`. A real need brings a role back as
enum-value + writer. Keeping dead plumbing would force a phantom always-empty UI facet
(banned by the "real empty states" rule); dropping it makes the role facet reflect real
data by construction.

**Correction (2026-06-20, found at execution — decision (B)):** the `custom` lane was NOT
dead. `logger.py:975` stamped run-level `RunContext.custom_metadata` into every
`MeasurementRecorded.custom`; the lane was the *persistence vehicle* for `custom_metadata` (a
real feature: `RunStarted.custom_metadata`, set via `RunContext`, read by `get_run`, consumed
by all three exporters). So `custom` was never a measurement *role* — it was **run-level
metadata mis-stored in a per-measurement lane.** Dropping the lane was right (it's not a
role); but `custom_metadata` must be **re-homed to run-grain persistence**: store it as a
JSON blob in **parquet file metadata** (mirroring the `environment_json` precedent —
`parquet.py:143` write / `:1175` read), sourced from `RunStarted`, no longer denormalized
onto measurements (`logger.py:975` stamping removed). This is added Phase-1 scope, authorized.

---

## 4. The query API

### 4a. Selecting a field — `FieldRef`

A recorded field is identified by `(role, name)`. In the **parametric viewer** you choose a
field for the Y axis and one for the X axis, so we need a selector. That selector is
**`FieldRef`** — a *reference to a named field*. (Name: "field" is role-neutral; `Param`
was rejected because "parameter" connotes input. It avoids `pydantic.Field`, which is
imported in the same module.)

```python
from litmus.queries import MeasurementsQuery, FieldRef

FieldRef.measurement("v_rail")   # the judged result named v_rail
FieldRef.output("v_rail")        # the observed output named v_rail
FieldRef.input("vin")            # the stimulus input named vin
```

These classmethod constructors are the everyday surface — terse, IDE-discoverable, no
import-alias needed, and no shadowing of the `input` builtin (which a module-level
`input()` factory would cause). This follows Altair's `alt.X(...)` / Polars' `pl.col(...)`
/ Django's `F(...)` "field reference" lineage.

**Member types are asymmetric — and `value_type` is NOT an enum.** `role` is a closed
`FieldRole` StrEnum (we own the domain: `input`/`output`/`measurement`). `value_type` is an
**open `str | None`** — `observation_kind()` returns 8 known tags (`uri`, `scalar:bool/int/
float/str/datetime`, `list`, `dict`) *plus* an unbounded `other:<typename>` fallback, and it's
stored as a free `VARCHAR` (not a DuckDB `ENUM`, unlike `outcome_kind`/`comparator_kind`). So
`value_type` can't be closed — it *reflects* arbitrary stored types rather than a domain we
define. Disambiguation picks from the value_types `describe`/`distinct` report as actually
present (a discovered set), so no static enum is needed.

`FieldRef` is a plain Pydantic model, so the classmethods are sugar over the base
constructor — three forms, all valid:

```python
FieldRef(role=FieldRole.OUTPUT, name="v_rail")   # plain constructor (the base)
FieldRef(role="output", name="v_rail")            # same — FieldRole is a StrEnum, coerces
FieldRef.output("v_rail")                          # classmethod sugar
```

The plain constructor is what the **wire boundary** uses: rebuilding from the flat
`*_name`/`*_role` scalars (§5) is just `FieldRef(role=y_role, name=y_name)` — Pydantic owns
the coercion, no parser. It's also the form for dynamic/loop construction.

**Shorthand:** because the measurement is the primary thing you plot, a **bare string is a
measurement-by-name shortcut**:

```python
q.parametric(y="v_rail", x=FieldRef.input("vin"))   # y is the v_rail measurement
```

A bare string resolves to `FieldRef.measurement(name)` *unless* it matches a fixed
infrastructure column (`vector_index`, `run_started_at`, `vector_retry`, `limit_low/high`),
which wins. Inputs and outputs always need the explicit `FieldRef` — a bare name can't say
which role, and that ambiguity is exactly what we're removing.

**Why measurement is the default role.** measurement carries limits + outcome → it's the
*judged unit* (the test result) → it's what analysis is overwhelmingly about → it's the
most-queried role → it earns the terse default. (Defaults are justified by primacy of use;
here importance *causes* that primacy — same fact, two sides.) This is consistent across
every default: bare string → measurement, `cpk` pinned to measurement, `pareto` groups by
`measurement_name`, and the fixed-column fast path is the measured value + limits + outcome.
It also reads naturally: measurement is the one role that never baked its name into a column
(`measurement_name` holds it as a value), so a bare `"v_rail"` = "the measurement named
v_rail" falls straight out. Input/output are the deliberate, explicit opt-in.

This mirrors Altair/Django, where a bare string is the field-reference shorthand and a short
wrapper is the explicit form ([Altair encodings](https://altair-viz.github.io/user_guide/encodings/index.html),
[Django queries](https://docs.djangoproject.com/en/6.0/topics/db/queries/)).

### 4b. `parametric()` returns points — nothing about how to draw them

```python
q.parametric(y=..., x=..., group_by=None, filters=..., limit=...) -> list[ParametricRow]
```

It returns `(x, y, group)` rows. **Scatter vs line is purely how the caller draws the same
points** — so it is *not* a query parameter (today's `chart_type=` mixed rendering into the
query and made the return type polymorphic). This follows the grammar-of-graphics split
Altair uses: the *mark* (scatter/line) is rendering; the *data* is the same either way.

### 4c. `histogram()` is its own method — it's a different question

A histogram isn't a chart style of "Y vs X"; it bins **one** field's values and counts
them — a real data computation (a *transform*, in Altair's terms), not a render choice. So
it's a separate method with its own return shape, instead of being folded into `parametric`
behind a `chart_type` flag:

```python
q.histogram(field=..., bins=30, group_by=None, filters=...) -> list[HistogramRow]
```

(Bar — average-per-X — likewise becomes a normal `group_by` + aggregate, not a chart flag.)
The net effect: `parametric` stops being four methods in a trench coat with a polymorphic
return, and each question has one clear method.

### 4d. Aggregates — field selection where you name a field, `role=` only to enumerate

The aggregates split three ways by how (or whether) they name a field — and `cpk` /
`pareto` are *not* the same shape (an easy conflation):

| Method group | Field selection |
|---|---|
| **Outcome / run aggregates** (`yield_summary` / `trend` / `retest` / `time_loss` / **`pareto`**) | none — they group and *rank/summarize* outcomes; no single field is selected. `pareto` ranks `measurement_name`s by failure count (`measurement_outcome='failed'`); its purpose is the ranking *across all*, so it takes no `FieldRef` |
| **Capability of a measurement** (`cpk`) | `str \| FieldRef` — picks *which* `measurement_name`. Role is pinned to `measurement`: cpk reads `measurement_value` + `limit_low/high`, which exist only for measurements (no limits → no Cpk). A non-measurement `FieldRef` is an **error** |
| **Enumeration** (`distinct_values`) | `role=` as a genuine filter — "list the names within this role" |

```python
q.cpk("v_rail")                       # bare string = the v_rail measurement
q.cpk(FieldRef.measurement("v_rail")) # explicit equivalent
q.cpk(FieldRef.output("v_rail"))      # ERROR — outputs have no limits, can't be cpk'd
q.cpk(part="PN-1")                    # None → all measurements (today's behavior)
q.distinct_values("name", role="output")   # enumerate output names
```

Want the *distribution* of a non-measurement field (no limits)? That's `histogram` /
`parametric`, where any-role `FieldRef` is valid — not `cpk`.

So the field selector (`str | FieldRef`) names a specific field in `parametric` axes and in
`cpk` (measurement only). Outcome aggregates (incl. `pareto`) select no field; `role=` as a
standalone filter survives only for enumeration (`distinct_values`). `FieldRole` (the enum)
remains cross-cutting (storage, filters, facets).

### 4e. `describe_columns()` stops emitting fused names — and curates fixed columns

It advertises fixed columns as plain strings plus measurement fields as structured
`(role, name)` entries, so the explore UI builds `FieldRef`s directly from real choices
(dropdown label `"v_rail (output)"` → `FieldRef.output("v_rail")`). No `out_<name>` strings
anywhere.

**It also stops dumping raw columns.** Today `describe_columns()` does a bare `DESCRIBE
measurements_materialized` and `_classify_columns` feeds *every* column into the Y/X/group
pickers ("users see all real columns") — leaking `run_id`, `session_id`, `slot_id`,
`station_id`, `station_name`, `part_id`, etc. as plot axes. That violates the
operator-facing-identifier and no-synthetic-identifier rules (UUIDs and admin columns are
not axes). The filter path is already curated (`MEASUREMENT_FACETS` with human labels); the
axis path was not. Fix: a **registry of plottable fixed columns with labels** (sibling to
`MEASUREMENT_FACETS`) — e.g. `run_started_at`→"Date", `step_outcome`→"Step outcome",
`vector_index`→"Iteration" — excluding identity/admin columns. No raw `DESCRIBE`
passthrough; measurement fields still arrive as `(role, name)`.

### 4f. Type coherence — a name resolves to one `value_type`, loudly

Inputs/outputs are polymorphic by nature (`configure`/`observe` take arbitrary values), and
across time the *same* name can carry different types — a real production fact, not a fixable
discipline problem. Measurements are exempt: `verify`/`measure` take `value: float|int|None`,
so `measurement_value` is a numeric fixed column.

The EAV already stores types **separated** — a `value_type` tag (`scalar:int`/`float`/`bool`/
`datetime`, `list`, `dict`, text) plus per-type value columns (`value_int`, `value_double`,
`value_bool`, `value_text`, `value_timestamp`, `value_json`). Nothing is blended at rest. So
the exact value-type set for a `(role, name)` under any filter is one cheap group-by:

```sql
SELECT value_type, COUNT(*) FROM measurements_dynamic
WHERE role=? AND name=? {scope} GROUP BY value_type
```

(The EAV tag is renamed `kind` → `value_type` in this task — see vocabulary boundary below.)

A query result column, however, must be one type. The bug today is that the old `out_*` /
`TRY_CAST` path silently collapsed mixed types (non-matching rows → NULL, dropped). The
honest read semantics — **no user discipline required**:

1. **One value_type in scope → auto-resolve, frictionless** (the common case).
2. **Many in scope → fail loud with the breakdown**, never silently pick:
   `output "v_rail" has 2 value_types here: scalar:float (1,204), scalar:str (3). Specify value_type=.`
3. **`value_type=` is optional on `FieldRef`** — auto when unambiguous, required only when
   actually mixed. `FieldRef.output("v_rail", value_type="scalar:float")`.
4. **`describe`/`distinct` report value_types per name** so the UI shows a picker *only* for
   polymorphic names; the wire form adds a `*_value_type` scalar alongside `*_name`/`*_role`.

This is the *honest* version of the read path Phase 1 already rewrites (we're tearing out
`_DynamicJoins`/the `TRY_CAST` regardless), so it rides in #52's core — not a new subsystem.

**Vocabulary boundary — the value-datatype tag is `value_type`.** A full `type`-vs-`kind`
audit (2026-06-20) found the codebase uses **`<noun>_type`** as the productive "type of
\<noun\>" pattern (`event_type`, `station_type`, `column_type`, `data_type`) and reserves
`Kind` for *variant/classification enums* (`FacetKind`, `ChannelKind`, `VerbKind`,
`outcome_kind`/`comparator_kind`). The value-datatype tag was the outlier — `kind` in the
measurements EAV, `data_type` in the channels store — two words for one concept.

**Decision: the value-datatype tag is `value_type` everywhere.** It beats both:
- The datum is already called **value** in both stores (channels `ChannelSample.value`; the
  EAV's `value_int`/`value_double`/… family — and the **V in EAV is Value**). `value_type` =
  "the type of the value" matches the field it describes; `data_type` describes a field named
  `value`, and `kind` doesn't match its `value_*` siblings.
- **`<noun>_type` convention** + the prefix disambiguates the overloaded "type".
- **Dodges the `type` builtin/keyword** (a bare `type` shadows the Python builtin and is
  SQL-keyword-adjacent — the prefix avoids both).

Rule for the `value_` prefix: **prefix to disambiguate an overloaded word.** `type` is
overloaded → `value_type`. `unit` is *not* overloaded and is already uniform across stores
(`ChannelSample.unit`, EAV `unit`, `MeasurementRecorded.unit`) → **stays `unit`**, no
`value_unit` (prefixing would break existing consistency for zero gain).

Scope: **EAV `kind` → `value_type` rides in #52** (same projection code as `side`→`role`).
The channels `data_type` → `value_type` rename is a **separate task** (tuned machine,
sign-off, and its `{shape}:{leaf}` value vocabulary may need reconciling). The `*Kind`
variant enums and `outcome_kind`/`comparator_kind` are a *different concept* (which-variant,
not which-datatype) and are **not** renamed.

The one wart left: DuckDB's `column_type` (`DOUBLE`/`VARCHAR`) — engine vocabulary surfaced
raw. So `describe_columns` reports **role-keyed fields by `value_type`** (our datatype) and
**fixed columns by their SQL `column_type`** (the engine's) — distinct keys, never one
ambiguous "type".

**Why the EAV uses typed `value_*` columns, not VARIANT** (benched,
`bench_at_rest_encoding.py` / `bench_measurement_storage.py`; see
[`measurement-storage-eav.md`](measurement-storage-eav.md)): typed columns win on every axis
— smaller on disk (VARIANT was 6% *larger*), native `UNNEST` projection (VARIANT 2.4× slower,
can't unnest a variant), scans ~125× faster than shredded-variant (through DuckDB 1.5.3),
lossless int (`value_int BIGINT`), engine-portable (typed columns exist everywhere;
VARIANT-in-parquet is an immature 2025 extension). The 5 mostly-NULL `value_*` columns
compress to ~nothing under zstd. This is *why* the EAV is the engine-neutral contract (§9
portability) — a VARIANT would tie it to DuckDB.

### 4g. Resource vs role — why there's no `OutputQuery`

The read surface is **resource-centric** (resource-oriented / RESTful best practice): one
query surface per *resource* — a distinct entity with its own storage and row shape. The
MCP layer already does this uniformly (`litmus_runs` / `litmus_steps` / `litmus_events` /
`litmus_sessions` / `litmus_channels` / `litmus_files` / `litmus_metrics`).

That fixes `FieldRef`'s boundary:

- **A query exists per resource** — Run, Step, Measurement, Channel, File, Event, Session.
- **Not per role.** Output/Input/Measurement are `FieldRole` values *within* the Measurement
  resource → **no `OutputQuery`**; outputs are `MeasurementsQuery` + `FieldRef.output(...)`.
  Role lives in `FieldRef`, never in a class name.
- **Not per redundant grain.** Vector-grain rows (incl. observation-only vectors) live in the
  measurements table → **no `VectorQuery`**; subsumed by `MeasurementsQuery`.
- **Grain among run → step → measurement** is the *client choice* (`RunsQuery` /
  `StepsQuery` / `MeasurementsQuery`), not a field attribute. Run/step fixed columns ride
  denormalized on the measurement row, so cross-grain axes already work
  (`parametric(y="v_rail", x="run_started_at")`).

---

## 5. Surfaces — the `(role, name)` shape on each one

The query API isn't only Python. The **aggregates** already cross Python + MCP
(`litmus_metrics`) + CLI (`litmus metrics`), all via flat scalar params (`part`, `station`,
…). Adding `role=` as one more flat scalar enum works on every one of those with no trouble.

`parametric`/`FieldRef` is **Python-client + UI-internal only today** — not in MCP, CLI, or
HTTP. But if/when it gets a cross-surface tool (CLAUDE.md requires MCP↔HTTP parity), a
nested `FieldRef` object is hostile to a URL query string, a CLI flag, and an LLM tool
schema. So the rule we bake in now:

**The wire form is always flat scalar pairs — never the nested object.** "Flat" = no
nesting, just scalar key=value fields. "`*_name` + `*_role`" = for each axis there is a
name scalar and a role scalar:

```text
Python:   q.parametric(y=FieldRef.output("v_rail"), x=FieldRef.input("vin"))
HTTP/MCP: y_name=v_rail  y_role=output   x_name=vin  x_role=input
CLI:      --y-name v_rail --y-role output --x-name vin --x-role input
```

Each axis is up to three flat scalars — `*_name`, `*_role`, and `*_value_type` (the last only
when disambiguating a polymorphic name). `FieldRef` + the classmethods + the bare-string
shortcut are **Python-client sugar** that collapse to those scalars at the boundary. The
union `str | FieldRef` never travels on the wire.

---

## 6. Storage

- **Drop the fused flat `in_/out_/custom_` parquet columns entirely.** The EAV table
  `measurements_dynamic`, keyed `(role, name)`, is the single source.
- **Rename the EAV `side` column → `role`**; normalize values `in`/`out` → `input`/`output`.
  Required for the one-vocabulary decision. Pre-release, no shim.
- **Rename the EAV `kind` column → `value_type`** (the value-datatype tag). Joins the
  `value_*` family it tags; matches `FieldRef`'s member 1:1. Channels' `data_type` → same
  word is a *separate* task (tuned machine). `unit` stays bare.
- The nested at-rest lanes stay `inputs` / `outputs`; the `custom` lane is removed.

---

## 7. Phases (clustered — review each before the next)

### Phase 1 — CORE (daemon + query client)
Drop the fused-column projection; make EAV `(role, name)` the query source; add `FieldRole`
+ `FieldRef`; rename `side`→`role` + `in`/`out`→`input`/`output`; rename EAV `kind`→`value_type`;
remove `custom`; split `parametric` (points only) from new `histogram`.

**`kind`→`value_type` is renamed at BOTH layers (decision (a), 2026-06-20).** `kind` is a
real per-entry string field both at-rest (the lane struct) AND in the EAV column — symmetric
representation, so it carries one name end-to-end. (Contrast `side`: at-rest it's structural
— *which* LIST column — so it's forced to differ from the EAV `role` *value*; `kind` has no
such constraint.) Parquet schema change → `rm -rf data/` migration (pre-1.0, no users).

| File | Change |
|---|---|
| `src/litmus/data/schemas.py` | **(was missing)** drop the `custom` lane (`("custom", _LANE_LIST)`); rename `_LANE_STRUCT` field `kind`→`value_type` |
| `src/litmus/data/backends/_row_helpers.py` | drop `custom=` plumbing; rename `LANE_FIELDS` `kind`→`value_type` + the encoder's `entry["kind"]` (`observation_kind()` producer name is cosmetic — left unless swept) |
| `src/litmus/data/_runs_duckdb_daemon.py` | drop `custom` + fused-column emission. **Kill "side" entirely — it's internal-only jargon (EAV column + this plumbing; nothing at-rest or user-facing is called "side"):** EAV column `side`→`role` (values `in`/`out`→`input`/`output`); rename identifiers `_IO_SIDES`→`_IO_ROLES`, `_DYNAMIC_SIDES`→`_DYNAMIC_ROLES`, loop vars + `'{side}' AS side`→`'{role}' AS role`, `_dynamic_attrs_map_expr(sides=)`→`roles=`, comments. Also EAV `kind`→`value_type` + `_LANE_SELECT` (`u.kind`), `_LANE_VALUE_VARCHAR` (`CASE e.kind`), `_dynamic_attrs_map_expr` CASE; review `measurement_io_schema` (see open points) |
| `src/litmus/analysis/measurements_query.py` | add `FieldRole`, `FieldRef` (members mirror EAV: `role`, `name`, optional `value_type`); `parametric(y/x: str\|FieldRef)` returns points; new `histogram(...)`; joins key on `(role, name)` from `FieldRef` not a prefix split; **resolver dispatch by role: `measurement`→fixed `measurement_value` column, `input`/`output`→EAV join** (EAV `role` domain is `{input, output}` only — there are NO `role='measurement'` EAV rows); **type coherence: auto-resolve single value_type, fail-loud on mixed (no silent `TRY_CAST`→NULL), `value_type=` to disambiguate**; `describe_columns` → fixed strings + `(role,name)` + per-name value_type set; `distinct_values` reports value_types; `cpk`/`pareto`/`distinct_values`/`summary_counts` gain `role=` |
| `src/litmus/analysis/measurement_facets.py` | new models live here (`FieldRef` etc.); `role` facet spec |
| `src/litmus/data/events.py` | drop `MeasurementRecorded.custom` |
| `src/litmus/data/backends/_event_accumulator.py` | drop `custom=` plumbing |
| `src/litmus/api/schemas.py` | drop `custom_*` read-back |

### Phase 2 — UI (explore page + facets)
| File | Change |
|---|---|
| `src/litmus/ui/pages/explore.py` | axis pickers emit `FieldRef` (label `"name (role)"`); "value" (the measurement) is selectable only when a measurement is scoped, else hidden (designs out the unscoped-mixing footgun); `_default_x/_default_y` stop string-matching `in_`/`out_`; role facet in the filter row |

### Phase 3 — EXPORTERS
CSV / HDF5 / JSON column naming → `name` (+ `role`), dropping `in_`/`out_` prefixes.

### Phase 4 — TESTS
Re-point every `out_<name>` / `in_<name>` assertion → `(role, name)` / `FieldRef`; drop
`custom_*` fixtures. Grep all offenders first, then fix per-file.

### Phase 5 — DOCS
`three-verbs`, `parquet-schema`, `query-api` reference → `role` + `name`; drop the
`out_<name>` vocabulary and `custom`. Regenerate marker-gated reference pages via
`scripts/generate_reference_docs.py --all`.

---

## 8. Open sub-points

1. **`dynamic_attrs` MAP keys — RESOLVED (A), 2026-06-20.** Blast-radius check found: the MAP
   (fused `in_`/`out_` keys) is read by `steps_query` (which *already* un-fuses → clean
   `StepRow.inputs`/`outputs` dicts) and `run_store.get_measurements` (which exposes fused
   `out_*`/`in_*` keys verbatim → `LitmusClient` / HTTP API / reports / UI). `measurements_query`
   does NOT use the MAP (reads the EAV). **Decision (A): keep the MAP fused as internal
   plumbing; #1 fixes only the `measurements_query`/EAV path.** A coupling landmine must be
   handled either way: `_DYNAMIC_SIDES` drove BOTH the EAV value projection AND the MAP key
   prefix — split into `_DYNAMIC_ROLES` (EAV values `input`/`output`) and a separate prefix
   mapping (MAP keys stay `in_`/`out_`). The `run_store.get_measurements` → API/client/reports/UI
   un-fuse is tracked as a **separate follow-up (task #6)**; `steps_query` is already clean.
2. **`describe_columns` wire shape.** Exact contract for advertising a `(role, name, value_type)`
   field to the UI — resolve in cluster 1b (query client).
3. **`measurement_io_schema` catalog keying** — currently keyed on the prefixed column name;
   move to `(role, name)`. Resolve in cluster 1b.

## 9. Scope guards (northstar — explicitly NOT in this task)

- **Multi-Y × multi-X grid** (combinatorial bins + a table under the chart) → **0.3.0.**
  `FieldRef` is built symmetric and list-ready so `y`/`x` widen `FieldRef → FieldRef |
  list[FieldRef]` with no rework. Build single-Y × single-X now.
- **Filter-idiom unification.** Aggregates take loose kwargs (`part=`, …); `parametric`
  takes a `FilterSet`. Unifying on one idiom is the right direction but touches all six
  aggregate methods — **deferred follow-up**, not this task.
- **Future roles** (`condition`, `config`, …) — the open `FieldRole` enum + string-stored
  `role` make a new role purely additive (enum value + a writer). An **alias** to an existing
  role (`condition` → `input`) is a thin presentation layer over the same stored value, not
  new storage. Neither is built now; starting with `input | output | measurement` keeps the
  surface maximally open. (A dropped `custom` likewise returns this way if a need lands.)
- **Other nuances → fixed columns.** Anything that isn't a role-keyed field can be a fixed
  column on the runs/measurement rows; the `str | FieldRef` split already carries those as
  the plain-string arm.
- **Backend portability** (see [`data-store-backends.md`](data-store-backends.md)). The EAV
  (`role, name, value_type, value_*`) is a normalized relational table — the **engine-neutral
  contract**. Only the nested `LIST<STRUCT>` at-rest encoding + the `read_parquet`/`UNNEST`
  projection SQL are DuckDB/parquet-specific. Because the query client reads only the
  daemon's projected views (never parquet directly — store-boundary rule), a move to e.g.
  Postgres is *re-implement the projection layer onto the same schema*, with `MeasurementsQuery`
  / `FieldRef` unchanged. Bounded, not drop-in; the EAV is what buys it. Northstar, not #52.
- **Unify resources under a consistent `*Query`** (separate cleanup, NOT this task). Today
  only Run/Step/Measurement have a `*Query`; Event/Channel/File are read via their `*Store`
  (and `queries.py` re-exports `EventStore` as a query peer). Resource-centric in spirit but
  not uniform in shape. Giving Channel/File/Event real `*Query` classes (demoting the store
  re-export) is its own architectural pass — tracked, not folded into the role redesign.

---

## 10. Progress log

- **2026-06-20** — Shaped over a long design session. Locked: `role` vocabulary
  (`FieldRole` open enum, `input`/`output`/`measurement`); drop `custom`; `FieldRef`
  selector (classmethods + bare-string measurement shorthand); `parametric` returns points,
  `histogram` separate, scatter/line is render-only; aggregates take `role=` filter; flat
  `*_name`+`*_role` wire form, `FieldRef` is Python-only sugar; drop fused flat columns,
  rename EAV `side`→`role`; symmetric/list-ready for the 0.3.0 grid but single×single now.
  Diary written for review. Recovered from the prior session (the shaped plan lived in a
  TaskList item wiped by `/clear`; reconstructed from transcript). Nothing executed yet.
- **2026-06-20 (cont.)** — Extended in review discussion: measurement is the default role
  (it's the judged unit → most-queried); `cpk` takes the field selector (measurement-pinned,
  errors on non-measurement), `pareto` is outcome-ranking (no field selector); naming
  principle (`FieldRef` for open user-named namespaces, flat names for closed system fields —
  `out_*` changed, `run_outcome` stays); fixed-column axis surface is curated (no
  identity/admin columns); **type coherence added to core** (`kind` optional on `FieldRef`,
  auto-resolve single kind, fail-loud on mixed, kinds surfaced in describe/distinct, `*_kind`
  wire scalar); `kind` is the value-type word, kept distinct from `record_type` (grain) and
  SQL `column_type`. Northstars logged: cross-resource `run.outcome` grammar, resource
  `*Query` unification (task #3), pareto "Top X by Y" generalization, backend portability
  (EAV = engine-neutral contract). Still pre-execution.
- **2026-06-20 — Phase 1 CORE complete** (Sonnet agents, Opus-reviewed each diff). 1a: storage/
  write/daemon `side`→`role` + `kind`→`value_type` (both layers) + dropped `custom` lane + dropped
  fused-column emission + decoupled `dynamic_attrs` MAP prefix (keys stay `in_`/`out_`, option A).
  (B): `custom_metadata` re-homed to parquet file-metadata JSON blob (environment_json precedent),
  both write paths, round-trip tested. 1b: `FieldRole`/`FieldRef` + `(role,name)` EAV joins +
  `parametric`→points + new `histogram` + `cpk`/`distinct_values` role-aware + `describe_columns`→
  `ColumnSchema` (curated fixed + `(role,name)` fields) + `measurement_io_schema`→`(role,name,value_type)`.
  Review caught + fixed: the `custom`-drop regression (B), dead `_field_sql_expr`, ignored `cast_as`,
  and restored `group_by: str|FieldRef`. Verified: 623 passed, ruff clean; 9 failures ALL expected
  Phase-4 test-updates (old `chart_type`/`in_freq`/`column_name`/`WHERE side='out'`). Next: Phase 2 (UI).
- **2026-06-20 — green baseline** (Phase-4 test-updates pulled forward, authorized). The 9
  failing tests updated to the new API/schema (`ColumnSchema`, `histogram()`, `FieldRef`,
  `role='output'`); `test_bar_aggregates` deleted (query-side bar agg removed — now a render
  concern). Stale `data/runs/_index.duckdb` (old `side`/`kind`) dropped so the daemon rebuilds
  on the new schema (the `rm -rf data/` migration, index only). Verified: 631 passed, 0 failed
  across measurements_query/data/steps_query/runs_query/schemas. Phase 1 fully landed + green.
  OPEN for Phase 2: bar-chart rendering (render-side average over parametric points, or drop bar).
- **2026-06-21 — Phases 2–4 complete** (Sonnet agents, Opus-reviewed each diff). P2 UI: explore.py
  rewired to `ColumnSchema`/`FieldRef` axis selectors, scatter/line/histogram/bar (bar = render-side
  avg, behavior-preserving), value_type picker for polymorphic fields, role facet; Playwright-verified
  rendering with live data (console-noise check deferred to screenshot/UI-audit pass #2). P3 exporters:
  flat `in_`/`out_` → `input_`/`output_` (CSV+HDF5); JSON `params`/`observations` left. P4 tests: the
  9 selected-suite failures fixed earlier; benchmark `test_perf_daemon` updated to `FieldRef`/`histogram`
  + 2 stragglers (`isinstance ColumnSchema`, inline `side`→`role`). Review caught: `_label_to_selector`
  dead code (removed); a `group_by`-role-field bug (numeric group rejected by `ParametricRow.group:str`)
  → fixed via a `_coerce_group` validator. Verified: full selected suite 2142 passed / 0 failed;
  perf-daemon green. `units`→`unit` confirmed Pyright-only (suite green) → task #7 downgraded.
  #1 code+tests DONE (Phases 1–4). Remaining: Phase 5 (docs).
- **2026-06-20 (cont. 2)** — Full `type`-vs-`kind` audit. Rule confirmed: `<noun>_type` =
  "type of \<noun\>" (event_type/station_type/column_type); `Kind` reserved for variant enums
  (FacetKind/ChannelKind/VerbKind/outcome_kind). The value-datatype tag was the outlier
  (`kind` in measurements EAV, `data_type` in channels). **Decision: value-datatype tag =
  `value_type`** (the V in EAV is Value; joins the `value_*` family; matches `FieldRef`
  members 1:1; dodges the `type` builtin/keyword). **EAV `kind`→`value_type` folds into #52**
  (same projection code as side→role); `FieldRef` member + kwarg + wire scalar = `value_type`
  (verbose, but only in the rare polymorphic-disambiguation case). `unit` stays bare
  (prefix-only-to-disambiguate; `unit` isn't overloaded). Channels `data_type`→`value_type`
  is a SEPARATE task (tuned machine, value-vocabulary reconciliation). Still pre-execution.

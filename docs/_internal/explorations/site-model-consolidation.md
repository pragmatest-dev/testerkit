# Site model consolidation — `site_index` always-present, session-scoped, baseline+augment connections

**Status:** design converged + scope decided **STAGED** (2026-07-01). The `site_index`-always-0
half lands in 0.3.0; baseline+augment is a post-0.3.0 follow-on. See "Scope decision" below.
Builds directly on `slot-to-site-rename.md` (which landed the rename + 0-based decision).
This contract closes the open questions that rename left — chiefly "is `site_index`
nullable?" — and unifies site-index resolution with fixture-connection resolution.

**Supersedes** the rename doc's implicit "single-UUT `site_index` may be null" behavior.
The rename doc's field table already said `site_index` is *"0-based, always present"*; the
code diverged (`int | None`, null by default). This contract makes the code match the doc.

---

## One breath

A **site** is an **execution lane** — always present, 0-based, default `0`, `--site`-choosable.
It is **session-scoped**: unique *within* one coordinated (orchestrated) session, freely
collidable *across* independent sessions. Parallelism is inferred from **session→runs
fan-out**, never from `site_index` being null or special. A fixture's **connections** are a
lane-agnostic **baseline** (`fc.connections`) optionally **augmented** per lane (`sites[i]`);
a run's wiring is `baseline ⊕ sites[site_index]`. No fixture → no connections (instruments
still come from the station), lane still `0`.

---

## The model (converged invariants)

| Concept | Rule |
|---|---|
| `site_index` | Execution lane. `int`, 0-based, **always present**, default `0`. `--site <index\|name>` chooses it; the orchestrator assigns it per worker. |
| Index assignment | `site_index` = **position in the `sites:` list** (`enumerate`), never authored. `name` is a **decoupled label**, not an ordering key — write sites in any name order; the index follows list position. **List order *is* the physical mapping** (site 0 = first physical seat): the author orders the list to match hardware, labels it for readability. `--site left`/`left=SN` resolves name→index; `--site 0`/positional `SN0,SN1` follows order — same index space. |
| Site scope | **Session-scoped.** Unique within one session (bijection); collisions *across* sessions are meaningless (runs are keyed by `session_id`/`run_id`). |
| Parallelism | Inferred from **`count(run_id) per session_id > 1`** (topological, no field needed). `site_count > 1` is a convenience mirror; `site_index` null-ness is **not** a signal (it can't be null anymore). |
| Connections | `fc.connections` = **baseline** wiring for *every* lane. `sites[i]` = optional per-lane **augment/override**. A run's wiring = `baseline ⊕ sites[site_index]`, **last-wins per connection name**. |
| No fixture | No connections; instruments resolve from the **station** (roles). Lane is still `0`. |
| Instrument contention | Governed by the **per-resource file lock** (machine-global, keyed by physical resource), **independent of `site_index`**. Two runs sharing a physical instrument arbitrate via the lock whether their site is `0`, `2`, or (formerly) null. |
| Bijection | Within a session: every serial maps to a **distinct** site, no site fed twice, no serial dropped. Enforced by **fail-fast rejects** (below), not silent dedup. |

---

## What changes from today

1. **`site_index` field `int | None` → `int` (default `0`).** `events.py:279` (`RunStarted`),
   the `_state` ContextVar getters, and the at-rest column stop being nullable. The null
   case becomes unrepresentable.
2. **Single site-resolution point feeds *both* metadata and connections.** Today metadata
   resolves from `--site`/env (`hooks._resolve_and_install_site`) while connection-flattening
   only fires in worker mode off the env var (`__init__.py:577-592`). Unify: one resolver
   produces the concrete `site_index` (`--site` → env → default `0`); connection-flattening
   keys on *that* resolved index. Result: `--site 2` single-process wires `sites[2]`, not just
   the label.
3. **XOR validator reversal.** `test_config.py:46-50` rejects `connections` + `sites` together.
   The baseline+augment model *wants* both: top-level `connections:` becomes the shared
   baseline, `sites[]` layer per-lane deltas. Validator flips from "reject both" to "sites may
   accompany a baseline." Bare `connections:` and bare `sites:` both remain valid; combined
   becomes valid too. **Author-facing sugar preserved** — nobody is forced to write `sites:`.
4. **Multi-site detection moves off null-ness.** Every `site_index is None` branch repoints:
   the UI parallel gantt gates on **session→runs fan-out** (which it needs anyway to fetch the
   sibling lanes); `is_multi_site`/`site_count > 1` serve the cheap per-row flag.
5. **Two explicit uniqueness rejects** (replace silent collapse):
   - `parse_serials`: duplicate site keys (`0=A,0=B`) → usage error, not dict-overwrite
     (`uut_provider.py:148`).
   - Fixture validator: duplicate non-null site **names** → config-load error
     (finding #7; today first-match wins, orphaning the rest).

---

## Implementation surface (review findings hung under this contract)

From the `/design-review` of the rename (2026-07-01). These are symptoms of the null branch
and the split resolver; the model above is the root fix.

| # | Finding | Resolved by |
|---|---|---|
| 1 | `site_index` null-by-default contradicts "always present" | change 1 (field → `int`, default 0) |
| 2 | `RunStore.get_run()` drops `site_index`/`site_name` for measurement-less runs; wasted parquet re-read | read straight off the `runs_materialized` row; drop the parquet re-open for these two (bug fix, independently valid) |
| 3 | int-parse-first resolver duplicated (`uut_provider` vs `hooks`) with diverging error text | change 2 — one `resolve_site_token()` + shared "known sites" formatter in `sites.py` |
| 5 | `LITMUS_FIXTURE_SITE` env var written, never read | change 2 — have `fixture_config` consume it (saves a redundant YAML load), or delete |
| 7 | no duplicate-site-name validation | change 5 (reject duplicate names) |
| 8 | gantt skips null `site_index`; opposite of STDF's null→0 | change 4 — gate on session fan-out; null case gone |
| 10 | `TestRun` lacks `site_index`/`site_name`, forcing ContextVar reach-around in `_row_helpers` | add the fields to `TestRun`, stamp in `RunScope.__init__`; uniform `test_run.X → row["X"]` |

Finding #6 (stale `_validate_connections_or_slots` method name) is a trivial rename, folded in
with change 3's validator work.

---

## Prior art (why always-present + session-scoped is correct)

- **STDF V4** — `SITE_NUM` is an unconditional `U*1` on PIR/PRR/PTR (pystdf field maps); there
  is **no null/absent site state**. Single-site always emits a concrete value. (Spec text says
  single-site → 1; real tooling shows 0-based `SITE_NUM=[0,1]` — always-concrete is certain,
  0-vs-1 is convention, we picked 0.)
- **NI TestStand TSM** — `TestSocket.Index` is 0-based; single-site is `MyIndex=0, count=1` — a
  concrete value, never absent. **Two independent TestStand sequences each report "socket 0"**
  — the industry-standard tool already treats socket index as **per-execution (session-scoped)**,
  exactly this model. (TestStand is a named Litmus migration path.)
- **OpenHTF** — models no site at all (parallelism = separate processes); orthogonal, but argues
  "if you keep the concept, make it concrete," not "allow null."
- **Instrument sharing across independent runs** — handled by the per-resource file lock
  (`instruments/locks.py`, keyed by physical resource, machine-global), *independent of site*.
  So null-vs-0 has zero bearing on connection/instrument contention.

Full research: session task (2026-07-01 prior-art sweep). Weak spots flagged: STDF spec quote
is secondary-sourced; TSM behavior is forum-corroborated; Teradyne/Advantest uncorroborated.

---

## Decided (do not re-litigate)

- `site_index` is **always present, default 0** — not nullable. The one meaning null uniquely
  encoded ("deliberately not in a fixture site") is **incoherent**: you can't be in a sited
  fixture without occupying a site, and a non-sited context is simply bare `connections:` / no
  fixture with the trivial lane 0. Null reserved a state that never occurs.
- "Ignorable when uninteresting" is the **win**, not a compromise — a present-but-ignorable
  field removes the null-handling branch that findings #1/#2/#8/#10 are all bugs *within*.
- Connection merge is **last-wins per connection name** (same shape as the marker cascade:
  inline < sidecar < profile).

## Scope decision — STAGED (2026-07-01)

**0.3.0 (now, rides tomorrow's wipe):** the schema-shaping half —
- Change 1 (`site_index` `int|None`→`int`, default `0`),
- Change 2 (one unified resolver feeding metadata **and** connection-flattening; `--site N`
  wires `sites[N]`; consolidates the duplicated int-parse-first logic),
- Change 4 (multi-site detection off null-ness → session→runs fan-out / `site_count`),
- Change 5 (the two uniqueness rejects: dup `--uut-serials` key, dup site name),
- and findings **#2** (get_run data-loss bug), **#6** (validator method rename), **#10**
  (`TestRun` site fields → uniform `build_run_metadata`).
- **XOR stays** — bare `connections:` is still site-0's wiring via a special-case in the
  resolver; `connections` + `sites` together remains a config error for now.

**Post-0.3.0 follow-on (0.3.x):** Change 3 — the **baseline+augment** fixture model (reverse
the XOR validator; `fc.connections` becomes the lane-agnostic baseline; `sites[i]` layers
per-lane deltas with last-wins-per-name merge). No schema impact, so it doesn't need the wipe.

## Open (decide at implementation)

- **`LITMUS_FIXTURE_SITE`:** consume it (saves the worker's redundant fixture-YAML reload) vs.
  delete it (finding #5, confirmed dead) — default to **delete** unless consuming is trivial.
- **`site_count` denormalization onto run rows:** convenience only (UI can derive parallelism
  from session fan-out). Keep or drop.

Carried by the `schema_version` scheme like its siblings — but note **data is being wiped for
0.3.0**, so no migration path is needed; lay the shape down directly.

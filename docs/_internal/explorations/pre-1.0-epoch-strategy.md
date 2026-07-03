# Pre-1.0 Epoch Strategy — Stay at 0.x, Battle-Test the Versioning Apparatus

**Status:** proposed (this session's decision — awaiting commit to direction)
**Date:** 2026-07-03
**Supersedes the framing of:** "freeze the at-rest schema at 1.0 at the 0.3.0 release" — `schema-versioning-migration.md` §5 and task **#52**.
**Keystone it elevates:** the **index epoch (#47)**, task **#53**.

---

## Decision

**The 0.3.0 release does NOT freeze the schema at 1.0.** The at-rest **schema version** (distinct from the package version, which stays `0.3.0`) stays on a **0.x line**. The thing that makes the release *safe* is not the freeze — it's the **index epoch (#47)**. We graduate to `1.0` **later**, as an earned milestone, once the schema design has settled *and* the versioning apparatus has real mileage.

## Why — the evidence from this session

A single afternoon of *looking* surfaced a pile of **known, schema-shaped, unsettled** design questions:
- The occurrence **`index`** (measurement/output ordinal) is only correct if a **single-sourced counter** runs across the harness (emit), the accumulator (reassembly), and the client **builder** — the builder currently does neither, so it produces grain-breaking rows.
- **Outputs diverge from inputs** semantically (outputs are *references* / claim-tickets to channel/file stores; inputs are self-contained values). Structurally identical *today*, but the seam is visible and likely to become structural.
- Projection warts: the prefixed `dynamic_attrs` MAP, the mislabeled `measurements_dynamic` EAV (holds inputs/outputs, no measurements).

Simultaneously the **safety net is incomplete**: the derived-index epoch (**#47**) is *designed but unbuilt and untested*. So today, a non-additive schema change is a **crash** (the `_index.duckdb` poison-pill we spent the day on), not a smooth version bump.

**Immature schema + untested machinery = a 1.0 freeze that is brittle by construction.** And under the release rule ("a *known* break blocks the freeze"), every design question we open converts straight into a blocker. At 0.x, those same discoveries are cheap: you break it, you move on, nobody was promised stability.

## Mechanism — `epoch = leftmost-significant semver component`

This is just the standard semver convention:
- **Pre-1.0:** the **minor** is the breaking epoch → `0.1`, `0.2`, `0.3` are each distinct epochs.
- **Post-1.0:** the **major** is the breaking epoch → `1.0 → 1.1` is additive; `2.0` is a new epoch.

One function — `epoch(v) = leftmost-nonzero component` — and the cadence switch at 1.0 falls out for free. No special-casing.

## What the apparatus already does vs. what #47 must add (verified)

- **Source-file classification is already epoch-agnostic.** `schema_dispatch` keys on a **whitelist** (`KNOWN_SCHEMA_VERSIONS`) plus an older/newer version-tuple comparison — not on a hardcoded `1.0`. An old-epoch parquet is classified older-unknown → routed into the store's **quarantine** path ("regenerate"). So "treat a 0.x bump as breaking" = keep only the current `0.x` whitelisted, register **no** back-adapter. Nearly free.
- **The derived index is the crash vector, and needs #47.** File quarantine does nothing for the `_index.duckdb` — a new-epoch daemon opening an old-epoch index crash-loops at view-creation. **#47** (index epoch: `_index.e{N}.duckdb`; a daemon opens **only** its own epoch's file, rebuilds from parquet if absent, ignores the others) is the keystone. `epoch` here is the same leftmost-significant component.

## The de-risking rationale (the point)

**0.x churn becomes a live battle-test of the exact path we'll bet on at the first real `2.0`.** Every 0.x breaking change rehearses the epoch → quarantine → index-rebuild → adapter-dispatch sequence. Freezing straight to `1.0` means the *first* time that path runs for real is a customer's `2.0` upgrade. Running it on every 0.x bump means that by the time we freeze, it's **proven** — the `1.0` stability promise is earned, not asserted.

## What this frees up for 0.3.0

Because breaking changes at 0.x are now **cheap and crash-safe** (via #47), the following are **no longer release blockers** — they land in future 0.x epochs:
- the at-rest `index` fields + the single-sourced occurrence counter (harness/accumulator/builder),
- the outputs-vs-inputs projection split, if/when outputs diverge structurally,
- the `dynamic_attrs`/EAV projection cleanups.

The `/explore` default-chart gap is a **non-breaking read/UI fix** (shape-aware default X) — unaffected by any of this, ships whenever.

## Concrete moves

1. **Un-freeze the schema stamp** — `schema_versions.py` current `"1.0"` → a `0.x` value (reverts the "reset to 1.0 at 0.3.0" change). *Package* version stays `0.3.0`.
2. **Build #47** with `epoch = leftmost-significant-component` (task #53) — the crash-safety keystone.
3. **Every 0.x bump = epoch bump** → the apparatus runs and is exercised.
4. **Graduate to 1.0 later** — when index/single-sourcing/output-divergence have settled and #47 has mileage. From then, the apparatus engages only at majors.

## Open decisions

- **Exact 0.x schema value** for the baseline (track the package minor, e.g. `0.3`? or a separate schema 0.x line?).
- **Where #47 lands** — 0.3.0 vs 0.3.1. It must exist **before the first cross-epoch upgrade** (any 0.4 breaking change); a fresh 0.3.0 install without it is fine (no prior epoch).
- **Reconcile the already-shipped 0.3.0 release** — the changelog/commit (`4099fc02`, pushed to `origin/main`) currently says "at-rest schema freeze at 1.0." If we adopt this, that framing needs amending (changelog + the schema stamp), which is a follow-up on top of the pushed release.

---

## Progress log

- 2026-07-03 — Strategy landed after a long design session that surfaced the schema is **not ready to freeze** (unsettled `index`/single-sourcing/output-divergence) and the safety net is **incomplete** (#47 unbuilt). Reframes the release: **ship 0.x + build #47**, not "freeze 1.0." Mechanism = semver `epoch = leftmost-significant`; 0.x churn battle-tests the apparatus so 1.0 is later earned. Supersedes the freeze framing in #52/§5. No code changed.

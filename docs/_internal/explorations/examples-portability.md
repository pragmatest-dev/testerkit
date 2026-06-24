# Example portability — copy-out + `litmus init --from-example`

Design record for making the bundled examples something a user can actually
**get and run** after installing Litmus, without disturbing their own project.

## Goal & priority (set by user, 2026-06-23)

- **Primary:** a user who has installed `litmus-test` can grab an example — from
  the GitHub repo, the sdist, or (eventually) the deployed package — drop it
  somewhere, and run it. It must bind to *their* installed `litmus-test` and keep
  its data isolated from theirs.
- **Secondary:** in-repo, the examples still test against **local HEAD** source
  (the uv-workspace behavior). Important, but secondary to the getting-started path.

## The three invariants any solution must hold

1. **No source override.** A copied example depends on the user's *installed*
   `litmus-test`, never a pinned source. → the example `pyproject.toml` must be
   clean PEP 621 (`dependencies = ["pytest", "litmus-test"]`, no `[tool.uv.sources]`).
2. **Data isolation.** A copied example writes runs to *its own* folder, never the
   user's data dir. → each example ships its own `litmus.yaml` with `data_dir: data`
   (relative). `resolve_data_dir()` resolves it against the nearest `litmus.yaml`
   (`data_dir.py:50`; `_find_project_config` walks ancestors nearest-first,
   `connect.py:487`). Already true for all 11 examples.
3. **In-project placement is `.examples/<id>/` only.** The dot prefix is
   load-bearing: pytest's default `norecursedirs` includes `.*` (verified in
   `_pytest/main.py`), so `.examples/` is skipped by the user's `pytest` collection
   and the example's tests never join the user's suite. Plain `examples/` would be
   collected — do not use it.

## Prerequisite — relocate the uv source to the workspace root

Today each `examples/NN/pyproject.toml` carries
`[tool.uv.sources]\nlitmus-test = { workspace = true }`. That violates invariant 1.

**Change (verified 2026-06-23):** move the source up to the repo-root
`pyproject.toml` and delete it from each example.

- Root `pyproject.toml` `[tool.uv.sources]`: add `litmus-test = { workspace = true }`.
- Each `examples/NN/pyproject.toml`: delete the `[tool.uv.sources]` block.

Verified by experiment: with the source at root and the example toml clean,
`uv lock` succeeds and example-01 still resolves `litmus-test` to `editable = "."`
(local HEAD). **Root-level workspace sources propagate to members** — so in-repo
local-HEAD testing (invariant secondary goal) is preserved while example tomls
become clean (invariant 1). Earlier belief that you "can't have both" was wrong:
it came from *deleting* the line rather than *relocating* it.

Why this makes copy-out clean: the root `pyproject.toml` sits two levels above
`examples/NN/`, so `cp -r examples/NN <dest>` can never pick it up. No example
folder carries a source pin; every copy is automatically clean.

## The two obtain-the-example modes

1. **Fresh standalone project** — `litmus init <name> --from-example <id>` scaffolds
   the example into a new named directory the user owns.
2. **Alongside an existing project** — `litmus pull-example <id>` (name TBD) writes
   only to `./.examples/<id>/`: hidden, isolated, and the *sole* sanctioned in-project
   path. The user runs their own suite normally; `cd .examples/<id> && pytest` runs
   the example in its own sandbox.

## Project-aware guard — don't overtake an existing project

A copy tool must not overtake a user's project; "don't clobber individual files" is
too weak. Before writing anything, resolve whether the destination is inside an
existing Litmus project (reuse `_find_project_config()` — ancestor walk for
`litmus.yaml`):

- **Fresh location** (no enclosing `litmus.yaml`, empty/new dir) → proceed.
- **Destination is / sits under an existing project root** → **refuse**, full stop
  ("`<dir>` is already a Litmus project; I won't scaffold over it. Use the
  `.examples/` mode to study an example alongside it.").
- **No silent merges, ever.** `.examples/` already covers "bring it in safely," so
  there is no reason to force onto a populated root.

## Open / to-do

- **Wheel bundling.** Examples ship in the **sdist** (`sdist.include` lists
  `examples`) but **not the wheel** (`wheel.force-include` bundles only `docs/`).
  So "copy out of the `pip install`ed package" doesn't work today. `init --from-example`
  pulling from an installed wheel requires bundling examples into the wheel (like the
  docs are) — decide on size/layout (`litmus/_examples/...`).
- **Command surface.** Decide `litmus init --from-example <id>` vs a dedicated
  `litmus pull-example <id>` for the `.examples/` mode (or one command with `--into`).
- **`05-product-spec/` orphan.** A stale `examples/05-product-spec/` exists beside
  `05-part-spec/` ("product" is the verboten DUT term; not in the workspace or
  `examples/README.md`). Delete as part of this work (separate from the docs sweep).
- **`examples/README.md` is stale** — advertises "Seven" but 08–11 exist on disk.

## Verification log (2026-06-23)

- uv root-source propagation: experiment above; `uv lock` clean, `editable = "."`.
- pytest skips dot-dirs: `norecursedirs` default includes `.*` (`_pytest/main.py`).
- data isolation: `resolve_data_dir` → nearest `litmus.yaml` (`data_dir.py:50`,
  `connect.py:487`); all 11 examples carry `data_dir: data`.
- examples in sdist not wheel: `pyproject.toml` `sdist.include` vs
  `wheel.force-include`.

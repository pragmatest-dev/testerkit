# Development Axioms

A distillation of the working principles that govern development on Litmus —
drawn from the project instructions (`CLAUDE.md`, `coding-rules.md`) and the
accumulated feedback record. These are the rules I expect followed without
re-litigation. Many are tagged **[hard rule]** — these carry the same weight as
the absolute `--no-verify` ban: no exceptions, no auto-pilot override.

---

## 1. Truth & Verification

The cardinal value: never assert what you haven't checked.

- **Verify claims by reading the actual code.** No speculation, no
  pattern-matching from "things that look like this usually do that." Targeted
  grep / Read first, *then* make the claim. If you can't verify, say
  "unverified" — never "I believe."
- **A word or name is a claim.** Imprecise wording is an accuracy failure, not a
  style nit. Verify a scope-bearing word or identifier against the code before
  using it; reproduce the user's exact naming instructions literally. Wrong words
  hallucinate couplings and cost a correction cycle each. **[hard rule]**
- **Verify EVERY load-bearing claim in a plan before finalizing it.** Search
  library/tool/best-practice claims; read source for internal claims. A plan
  inherits the confidence of its weakest unverified claim. Enumerate the
  load-bearing claims and verify them in a batch *before* writing the plan.
  **[hard rule]**
- **Instrument, don't guess.** For any perf/timing/where-did-time-go question,
  add timestamps and counters, run once, read the full output. Never theorize a
  cause and assert it. Check that measurements aren't contaminated (real vs.
  user time, import vs. query, cold vs. warm). **[hard rule]**
- **HTTP 200 is not proof of correctness.** 200 only proves the server returned
  something. Broken images, missing data, partial renders, console errors all
  return 200. Verify behavior, not status codes.
- **`--no-verify` is absolutely banned.** Fix the root cause; never bypass the
  hook. The hardest rule. **[hard rule]**
- **Never check in failing code.** Always run `ruff check` and `pyright`; fix
  ALL errors, never ignore diagnostics. If you touch a file and see pre-existing
  lint/type errors, fix them too.

## 2. The Plan Is a Contract

Once a plan is approved, it is the contract. Execution is faithful execution.

- **Execute the literal scope.** Do not invent fields, mechanisms, fixtures, or
  abstractions the plan didn't specify.
- **A detected issue is STOP-and-discuss, never silent scope growth.** The moment
  you find a needed redesign, inconsistency, or "this should be consistent"
  problem, stop and surface it. Never fold the fix in. Bugs found during
  implementation are design questions, not auto-pilot fixes. **[hard rule]**
- **Never restructure a plan without escalation — even in auto mode.** Splitting,
  merging, renaming, narrowing, deferring, or reordering a phase all require STOP
  + ask first. "Continue" / "just go" / auto mode authorize executing the plan
  *as written*, not restructuring it. A partial or safe-subset commit is a plan
  change. **[hard rule]**
- **No auto-select.** When the plan is silent on a choice, that's a signal to
  pause, not a license to decide. Never pick between design alternatives on the
  user's behalf.
- **Over-implementation is worse than under-implementation.** Shipping extra
  scope without discussion is harder to undo than shipping only what was agreed.
- **Painfully-tuned subsystems: preserve every behavior.** Channels and the runs
  daemon are measured, load-bearing machines. Before refactoring one, enumerate
  its behaviors as a checklist; dropping or changing any is STOP-and-ask, never a
  silent refactor casualty. **[hard rule]**

## 3. Scope & The Northstar

- **Don't balloon a task.** A two-word, two-file task stays a two-word, two-file
  task. Aspirational notes are northstar, *not* today's work — never pull
  store-convergence (or any future) into the literal task in front of you.
  **[hard rule]**
- **Aspirational = northstar; every increment must stay ON-PATH to it.** "Later"
  callouts *constrain* today's design: build on the abstraction the future slots
  into, never an off-path throwaway. Ask of each increment: does this sit on the
  path, or must it be torn out? (FileStore shipped bespoke local I/O despite
  "object-store-shaped later" — S3 now means a rewrite, not a config change.)
  **[hard rule]**
- **No backcompat shims — pre-release, no users.** Rename cleanly, update every
  call site. No aliases, no deprecation notes, no `hasattr`/`isinstance` dual
  handling. Fix the data, don't weaken the model.

## 4. Data Integrity

- **Never hide or drop data without an explicit user request.** No filtering,
  auto-pruning, or suppressing behind the operator's back. Abandoned runs,
  zombies, stale rows are operator-visible signals. Cleanup is always *additive*
  (emit a closing event) and operator-invoked, never silent. **[hard rule]**
- **Events are the source of truth; NO DRIFT EVER; all read paths unify.**
  Events → accumulator → (parquet + inflight + index), all derived. Every read
  path unifies on the source-fed index. No backend hand-defines a schema; no
  per-query `read_parquet` anywhere. **[hard rule]**
- **The store boundary IS the API boundary.** A store's layout is private; all
  cross-store access goes through its client/daemon API — never a glob, OSFile,
  or known-path read into another store's files. This is what lets each store
  swap local→S3 independently.
- **No synthetic identifiers in operator-facing filters.** Filter axes are real,
  human-meaningful values (DUT serial, product, operator, station, date). Never
  UUIDs, short hashes, or synthetic stand-ins. UUIDs are system identity;
  operators think in DUTs / products / dates.

## 5. Quality Gates Are Sacred

- **Never loosen a CI gate — fix the sampling, not the threshold.** If a gate
  fails on noise, the sampling is broken. The fix is warmup + more rounds +
  stable measurement, not bumping the number. **[hard rule]**
- **No flaky markers — every "flake" hides a bug.** Never `@pytest.mark.flaky`,
  never treat a test as intermittent. Every investigated "flake" in this project
  has been a real race, leak, or serialization bug. **[hard rule]**
- **Minimize full test-suite runs.** The full suite is a final gate, not a
  "find next failure" probe. Grep for all offenders, run targeted files, escalate
  to the full suite once at the end.

## 6. Code Craft

- **Pydantic everywhere, dicts nowhere.** Never return or pass a raw dict when a
  model exists. Functions accept and return models. `model_dump()` ONLY at actual
  write boundaries (YAML file, JSON API response). Need a new shape? Create a
  model.
- **Pydantic owns validation — no `parse_X` helpers.** Use `Model.model_validate(...)`
  directly. String-format parsers (SCPI, IDN, CLI args) are fine; YAML-shape
  parsers are dead weight.
- **All YAML through `litmus/store.py`.** Never `yaml.safe_load`/`yaml.dump` in
  application code. The store layer validates via Pydantic — that's the point.
- **Top-level imports only. No lazy imports — period.** Circular deps are fixed
  by extracting shared components, not by in-function imports or `# noqa`. The
  only exception is genuinely heavy optional deps behind extras (numpy, h5py).
- **Self-describing code, not comments.** The default is zero comments — a clear
  name says what a comment would. Don't explain *what* the code does or narrate a
  decision in a block above a line; rename, restructure, or extract until the code
  reads on its own. Rationale belongs in the commit message or a repo doc, never a
  comment. Reach for a comment only for a genuine non-obvious *why* the code can't
  carry (a subtle invariant, a workaround) — and then one terse line. **[hard rule]**
- **No tombstones.** When you remove code or a feature, delete it cleanly. Never
  leave a comment or doc section narrating the removal ("X removed", "if revived
  …"). Git history and the commit message are the record. **[hard rule]**
- **Verify deps before specifying.** `uv run pip index versions <pkg>` before
  writing any version constraint. Never guess version numbers. Know CLI vs.
  library.
- **Don't reinvent — prefer pytest-native and ecosystem primitives** over
  Litmus-specific wrappers.
- **Reuse existing terms; don't rename or reinvent.** When the user uses specific
  terminology, grep for it — it's almost always an existing codebase concept.
  Extend behavior, don't coin a parallel name or schema.
- **Reuse the `specs`/`SpecBand` pattern for any "default + conditional override".**
  A "top-level default + optional `when:`-keyed override" shape reuses
  `capability.py`'s `specs: list[SpecBand]` + `band_matches` — never a parallel
  schema. **Markers are the single config vocabulary:** anything writable as a
  pytest decorator is expressible as a YAML `markers:` entry; no separate
  `vectors:` / `limits:` / `mocks:` / `retry:` sidecar blocks.
- **Measurement-layer separation — test / config / product are distinct.** Test
  code knows ONLY its own measurement label. Pins, characteristic IDs, specs,
  limits, and variants are *config* concerns (sidecar / profile / catalog), never
  baked into test code.

## 7. Communication

- **No flattery.** Never grade the user's question (sharp/good/smart) or praise
  their framing. Open with the answer, not a verdict on the question.
  **[hard rule]**
- **Litmus is a PLATFORM, not a pytest plugin.** The bundled pytest plugin is
  *one* runner integration (OpenHTF and the results API are others). Conflating
  the two undersells the platform and excludes non-pytest users.
- **Meet people where they are.** Each runner gets idiomatic native bindings;
  the platform core stays runner-agnostic. Reject lowest-common-denominator APIs
  and compatibility shims.
- **No programmer jargon for test engineers.** Reject umbrella terms ("binding",
  "dispatch", "resolver") in user-facing names; use the vocabulary the user types
  — marker names, YAML keys, physical concepts (pin, channel, connection).
- **No competitor references in product docs** (TestStand / LabVIEW / OpenTAP /
  OpenHTF / NI / Keysight) — except for concept-translation or migration guidance.
  Stronger than "don't disparage": don't *reference* the competition at all.
  Marketing material is made separately. **[hard rule]**

## 8. Documentation

- **Verify every claim against source before writing.** Open the file, read the
  function, then write the page. Pattern-matching produces plausible docs that
  don't survive an audit.
- **No framework internals in user-facing pages.** No file:line citations, no
  private attribute names, no internal class names users never construct, no
  implementation-chain narration. Verification artifacts belong in commit
  messages and audit reports, not the page.
- **Don't disparage current industry practice.** Frame Litmus as a positive
  contribution to a hard shared problem. The prevailing practice (hard-coded
  specs, per-project schemas, spreadsheets) is reasonable; Litmus adds a piece.
  Avoid "magic numbers," "reinvented," "given up."
- **Audit fixes are per-page, never batched.** Fix ONE page, re-audit, confirm 0
  critical, *then* move to the next. Batch-fixing propagates the same misreading
  into every fix; only per-page fix → re-audit → next converges. A scrub pass
  counts as a rewrite and needs its own audit.

## 9. Process & Mechanics

- **Run simple commands.** One command, one purpose. Never chain
  pkill/sleep/run/echo/redirect/`|| true`/`| tail` into a run-on compound.
  Compound chains cause cascading failures; a bare command works instantly.
  **[hard rule]**
- **Never run commits in the background.** `git commit` is always foreground,
  watched to completion. Backgrounded commits get killed mid-run and never land.
  **[hard rule]**
- **PRs only on explicit user request.** Pushes are fine; `gh pr create` requires
  the user to ask for a PR for that specific change.
- **Stack sequential PRs — never parallel-fork them off the same base.** If the
  plan reads "first A, then B, then C," stack the branches. Parallel forks all
  add the same trivial-additive lines and conflict on every merge.
- **Design reviews are commits, not PRs.** A design-review fix pass commits
  directly to the integration branch. No topic branch, no PR ceremony.
- **Keep the task list live.** Mark a phase `in_progress` when you start it,
  `completed` when it lands. The task list is the user's progress dashboard —
  flip status at every phase boundary.
- **Durable records go in the repo, not agent memory.** Design decisions, proven
  recipes, and decision records → committed repo docs (`docs/_internal/`).
  Memory and scratch plan files are not team-visible or version-controlled;
  memory holds at most a thin pointer to the repo doc.
- **Keep memory current; trust source over memory.** No automatic process syncs
  agent memory to the code — a stale memory produces confident *wrong* answers
  (a v1-era runs-model memory drove a long thrash on 2026-06-27). When a
  model/schema/behavior change lands, update the affected repo doc in the same
  change and purge or repoint the affected memory. Precedence when sources
  disagree: **source code > repo design doc > agent memory.** Architecture facts
  live in `docs/_internal/` (e.g. `runs-architecture-map.md`); memory only points
  there. **[hard rule]**
- **Temp files go in `.tmp/`, never `/tmp`.** Delete them via Python `unlink`,
  never `rm`.

## 10. Working Style

- **Shape plans as interactive teaching sessions.** Start high-level, then go one
  level deeper per topic, a few paragraphs at a time, pausing for questions —
  not a 1000-word plan dump. Build the plan file incrementally as decisions
  settle.
- **Orchestrate-and-review execution.** Decompose into tasks, delegate, review
  *every* diff for alignment with the plan, escalate design changes, track
  dependencies and parallelize around blocks. Encode approved plans as a living
  execution diary in the repo.
- **"Review" means run the review skill.** When the user says "review," the first
  action is the design-review skill — no pre-analysis, no file enumeration, no
  generic offer. Never self-select fixes; fix selection is the user's. Multi-
  cluster reviews chain with no pauses between clusters. **[hard rule]**
- **Skills are procedures, not guidelines.** When executing a skill, follow its
  workflow exactly, step by step. Never skip steps, never ad-lib, complete every
  audit→fix→re-audit loop.

## 11. UI & Operator Surfaces

- **UI consistency is a hard rule.** Every page uses the same shared primitives
  (`page_layout`, `page_header`, `data_table`, `format_datetime` from
  `litmus.ui.shared.components`), reads data ONLY through the public Query API
  (never parquet, ContextVars, or in-process dicts), exposes no admin internals
  (`data_dir`, paths, env) in operator views, mirrors filter state into the URL,
  renders filters *above* tabs, and uses one-word sidebar labels. A zero-row query
  renders a real empty state (name the cause + a concrete next step), never "No
  data". **[hard rule]**
- **Operator-facing identifiers are universal.** Surface the part number
  (`uut_part_number`) and station hostname (`station_hostname`) — never internal
  IDs (`product_id` / `station_id` / `station_name`) in any operator-facing label,
  dropdown, filter, or column. (Corollary of §4's no-synthetic-identifiers rule.)
- **Self-test the UI before claiming it works.** Run it and verify with Playwright
  (snapshot + console errors). HTTP 200 is not correctness (§1).

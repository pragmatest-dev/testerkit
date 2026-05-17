# API stability and versioning across Litmus's contract surfaces

A guide to the public surfaces Litmus exposes, what each one locks at 0.1.0, what evolution is free vs. expensive, and which industry patterns inform our choices. Survey-grounded; recommendations follow each section.

## Why this doc exists

Litmus has more than one public contract. They're stacked, and they look like this today:

```
storage (parquet schema)
    ↓
DuckDB silver tables (regenerable)
    ↓
Python analysis.* classes (internal)
    ↓
HTTP API + CLI + MCP tools  ← the contract surfaces external code touches
```

If the layers don't separate cleanly, locking the top one locks every layer beneath. That's the situation today: HTTP endpoints largely return `internal_pydantic.model_dump()`, so HTTP shape mirrors Python shape mirrors DuckDB shape mirrors parquet shape. They all lock together.

This document surveys how comparable projects handle each surface, then maps the patterns to Litmus's specific case so we know what to commit to at 0.1.0 and what to leave room for.

## The cross-cutting lessons

Six patterns recur across every comparable project surveyed:

1. **Field IDs / stable identifiers beat names.** Apache Iceberg uses field IDs in Parquet metadata so renames are metadata-only; Avro uses positional/named matching; event-sourcing systems use revision numbers. Names are documentation; IDs are contracts.

2. **Additive change is universally safe; renames are universally the dividing line.** Every system surveyed handles `add column` / `add field` / `add flag` cleanly. Every system gets harder when you rename. The systems that handle renames cheaply do it via stable IDs underneath.

3. **Date-based versioning is the consensus answer for "we have to version, but can't break clients."** Stripe pioneered, GitHub adopted (2022), Twilio uses it in-path, MCP uses it at the protocol level. Pattern: client pins a date, provider maintains transformation chains internally, client opts into upgrades on its own schedule.

4. **"Evolution before versioning" is the dominant 2026 message.** Phil Sturgeon, Zalando, Microsoft, Speakeasy, Milan Jovanović all converge: versioning is the failure mode of evolution, not the default release engineering. Design every change to be additive; reserve versioning for cases where additive is impossible.

5. **Pre-1.0 stability is a discipline, not a guarantee.** Semver 2.0 §4 explicitly says 0.x has no stability promise; ecosystem practice (Cargo, npm caret) treats minor in 0.x as effective-major. The signal is two levels (minor=breaking, patch=safe) instead of three.

6. **Tool descriptions are part of the prompt for LLM-facing systems.** Tool names + descriptions in MCP servers feed directly into agent context. Industry hasn't converged on tool-level versioning yet, but practitioners are pushing toward "vendor at build time, lock at the agent level."

## Industry survey by mechanism

### HTTP API versioning — five mechanisms in production

| Mechanism | Example | Strengths | Weaknesses |
|---|---|---|---|
| **URL path** (`/v1/foo`) | Kubernetes (`v1alpha1`/`v1beta1`/`v1`), AWS API Gateway | Cache-friendly, visible in logs, trivial routing. K8s stability tiers (alpha/beta/GA) baked in. | Forces big-bang version cuts; "v2" becomes a marketing event. |
| **Vendor media type** (`Accept: application/vnd.foo.v3+json`) | GitHub (old) | URI stays clean; applies to payload schema only. | Invisible in normal exploration; harder to test ad-hoc. |
| **Custom header date** (`X-GitHub-Api-Version: 2022-11-28`) | GitHub (current) | Default-on; unsupported → `410 Gone`. Stripe-style versioning without query-string overhead. | Header-handling more complex on client side. |
| **Query param** (`?api-version=2024-01-01`) | Azure services, Salesforce | Same URI = same resource. Microsoft-recommended when URL paths must stay stable. | Cache key explosion; some proxies strip query strings. |
| **Date-based** (`Stripe-Version: 2024-XX-XX`) | Stripe, Twilio, MCP protocol | Clients pin a date; provider maintains internal transforms. Decade of backward compatibility maintained inside Stripe, not pushed onto clients. | Heavy provider-side investment in transformation infrastructure. |

**Practitioner consensus (Sturgeon, Zalando, Microsoft, Speakeasy):**
- Default to **additive evolution** within a stable surface.
- Version only at semantic breakpoints, never as routine release engineering.
- Zalando: "discourages versioning by all means."
- Phil Sturgeon: "API Versioning Has No 'Right Way'" — the right answer depends on consumer count, churn tolerance, who eats the upgrade work.

**What people regretted:** Stripe's pre-2017 era and AWS CLI v1→v2 are the canonical "we did a major version cut and it cost us" examples — Stripe responded by inventing date-versioning; AWS responded by building `aws-cli-migrate` to lint scripts. Salesforce's old REST versions (21.0–30.0) lingered for years as long-tail technical debt.

### Lakehouse / Parquet schema evolution

The three formats compared:

| Format | Add | Drop | Rename | Type widen | Type narrow | Partition evolve |
|---|---|---|---|---|---|---|
| **Apache Iceberg** | ✓ | ✓ | ✓ (free, via field IDs) | ✓ | ✗ | ✓ (metadata-only) |
| **Delta Lake** | ✓ | with column mapping | with column mapping | ✓ | ✗ | Liquid Clustering |
| **Apache Hudi** | ✓ | rewrite | rewrite | ✓ | ✗ | ✗ |

**Avro / Confluent Schema Registry compatibility levels** are the standard taxonomy:

- **BACKWARD** (default): consumer with new schema reads data produced with previous schema. Allows: delete optional fields, add optional fields with defaults. Forbids: adding required fields, deleting required fields. Lets you upgrade consumers first.
- **FORWARD**: producer with new schema, consumer with old schema can read it. Inverse permissions.
- **FULL**: both BACKWARD and FORWARD. Most restrictive.
- **\*\_TRANSITIVE** variants hold across all prior versions, not just N-1.
- **NONE**: explicit opt-out.

Avro lacks an `optional` keyword; convention is `["null", "T"]` union with `"default": null`.

**`union_by_name=true` (DuckDB, Spark mergeSchema)** gives you cheap reader-side schema evolution for additive-only schemas: missing columns become NULL. It does not give you rename detection, type promotion across files, or partition evolution.

**Lakehouse community converged on:** add is always safe; drop is "safe" but lossy; rename is the dividing line; type promotion is OK if strictly widening; type narrowing is forbidden without rewrite.

### CLI evolution

Real conventions across mature CLIs:

- **kubectl**: only formal written deprecation policy in the set. GA flags must function for ≥1 year or 2 releases (whichever longer). Admin-tool flags get 6 months / 1 release.
- **git**: extremely conservative. Documents `BreakingChanges/2.47.0` etc. Removals slow: warnings → require explicit opt-in flag (`--i-still-use-this`) → finally error. Major version cuts very rare (2.0 in 2014, no major since).
- **AWS CLI**: went big-bang from v1 to v2; cost was building a static linter (`aws-cli-migrate`). Canonical example of "the v2 release was so expensive we built tooling specifically to manage the cut."
- **Docker**: restructured subcommand groups in 1.13 (2017); old commands kept working as aliases. `docker ps` still works a decade later.
- **gh (GitHub CLI)**: additive-driven. New flags ship; deprecations announced on the GitHub Changelog blog with concrete EOL dates.

**Conventions that emerged across the set:**
- New flags are always safe with sensible defaults.
- Removal of flags is slow and announced — minimum one release of warnings.
- Subcommand restructuring is best done as additive aliasing (Docker pattern), not replacement.
- Don't rely on flag-prefix abbreviation in scripts (universal footgun).

### MCP tool versioning

Young, thin consensus.

- MCP itself uses **date-string protocol versioning** (`2025-06-18`, `2025-11-25`), negotiated at handshake.
- **No official versioning recommendation for individual tool names yet.** SEP-986 (the first tool-name standardization proposal) suggests aliasing for renames with deprecation warnings — same Docker-style pattern.
- Vercel's blog on MCP-to-AI-SDK explicitly flags the problem: *"Tool names, descriptions, and argument schemas become part of your agent's prompt and can change unexpectedly without warning."* Their proposed fix: **vendor tool definitions at build time** rather than discovering at runtime.
- Stytch's MCP-vulnerabilities writeup elevates tool-description stability to a near-security property — descriptions changing under an agent is not just stability but behavior-changing.

**State of consensus:** no equivalent of Stripe's date-versioning at the tool level yet; the burden of stability is implicit and real; practitioner moves are namespacing/prefixing, build-time vendoring, and deprecation aliases when renaming.

### Event-sourced schema evolution

Greg Young's "Versioning in an Event Sourced System" is the reference. The five tactics:

1. **Versioned events** — `OrderPlacedV1` / `OrderPlacedV2`, or `version: 2` on a single type.
2. **Weak schema (tolerant reader)** — JSON-style: copy fields by name, default missing, ignore unknown. **Two strict rules**: nothing renamed, semantic meaning unchanged.
3. **Upcasting** — pluggable middleware that transforms old events to latest shape on read. Application code only sees latest. Axon Framework is the canonical implementation.
4. **In-place transformation** — rewrite events in store. Heavy; breaks the event-sourcing axiom.
5. **Copy-and-transform** — replay-with-transform from old stream into new; switch consumers. Big-bang reshape that preserves the old store.

**Greg Young's principle:** *"A new version of an event must be convertible from the old version. If not, it's not a new version, it's a new event."*

**The 2021 empirical study (arxiv 2104.01146)** — schema evolution is *the* dominant operational pain in event-sourced systems. Teams overwhelmingly start with weak-schema + tolerant-reader and most never need anything more. The teams that get burned are the ones that **renamed fields** or **changed field semantics**. The discipline is social, not technical.

### Pre-1.0 versioning

- Semver 2.0 §4: 0.x has no stability promise. *"Anything MAY change at any time."*
- Cargo's resolver and npm's caret: an update is allowed only if it doesn't change the leftmost non-zero number. So in 0.x, **minor becomes effective-major**.
- *Effective Rust* Item 21: staying at 0.x forever reduces semver expressivity; you only get two stability signals instead of three.
- **dtolnay's semver-trick**: when forced into a coordinated upgrade across the dependency graph, publish 1.0 with the break, then publish a final 0.x.y+1 that re-exports the 1.0 surface. Used by `proc-macro2`, `syn`. Escape hatch when you hit deadlock.

## The 0.x → 1.0 framing for Litmus

Litmus 0.1.0 is intentionally a feedback release for early adopters before a 1.0 stability commitment. The semver convention does some signalling for us: minor bumps within 0.x may break, and consumers using `^0.1.0` resolution pin to that minor. So before applying the survey patterns to Litmus, we have to separate what *actually* needs stability pre-1.0 from what we're free to evolve.

**Hard contracts** — additive-only even pre-1.0. Breaking changes affect data on disk or running deployments; early adopters can't easily roll back, and silent breakage corrupts their state.

- **Parquet artifact** — early adopters' lakehouse pipelines ingest these files; broken columns mean broken historical data.
- **Event WAL** — events are immutable on disk; renaming a field in code makes historical events unreadable on the next run.

**Soft contracts** — release-noted breakage acceptable in 0.x. Breaking changes affect consumer code but not their data or deployments; early adopters adapt scripts/agents/clients with notice.

- **HTTP API** — JSON shapes; consumers update their HTTP clients.
- **CLI** — flags and command names; consumers update their scripts.
- **MCP tools** — tool names and schemas; consumers update their agent prompts.
- **Python `analysis.*`** — internal; not a contract.

The split tells you what each surface's recommendation should look like: hard contracts get held strictly even pre-1.0; soft contracts evolve with release notes pre-1.0 and get formal versioning infrastructure (path prefixes, deprecation runways, written stability policies) at the 1.0 cut.

The 1.0 ceremony itself is when we *introduce* the formal infrastructure — adding `/api/v1/` path prefix, publishing a deprecation policy, stamping the stability commitments. Pre-1.0, those things being absent is itself the signal that we haven't yet locked.

## Litmus's contract surfaces

Each surface analyzed below gets three things: the **0.x stability promise** (what we don't break in minor bumps even before 1.0), **free evolution** (what's always additive), and **the 1.0 lock plan** (what formal infrastructure ships when we tag 1.0).

### 1. Parquet artifact (lakehouse interop) — HARD contract

This is the strongest pre-1.0 promise we make. Lakehouse pipelines (Snowflake / Databricks / Trino) ingest parquets directly; breaking the schema breaks their historical data, not just their next pipeline run. We hold this even pre-1.0.

**0.x stability promise:**
- `SCHEMA_VERSION = "1.0"` stays additive only.
- Column names, types, nullability, and the `record_type` discriminator (`'run'` / `'step'` / `'measurement'`) don't change.
- On-disk path pattern (`data/runs/{date}/{timestamp}_{serial}.parquet`) and `_ref/` sidecar convention don't change.

**Free evolution within `SCHEMA_VERSION = "1.0"`:**
- **Add columns** — already automatic via daemon `ALTER TABLE ADD COLUMN IF NOT EXISTS`; readers use `union_by_name=true`.
- New dynamic `in_*` / `out_*` columns are infinitely additive (the schema reserves the prefix space).
- Adding new `record_type` values is borderline — consumers may filter by known values; flag in release notes if we do this.

**What requires a real break (deferred):**
- Drop columns (lakehouse readers expect column stability for catalog interop)
- Rename columns (our parquet has no field IDs — rename = break)
- Type narrowing (universally forbidden across all lakehouse formats)

If we ever need any of these, it ships as `SCHEMA_VERSION = "2.0"` with a new on-disk path pattern letting old and new coexist, a migration tool that reads v1 parquets and emits v2, and a deprecation window where `RunStore` reads both. Likely a 1.0+ event.

**1.0 lock plan:** the additive promise becomes a published commitment in `docs/concepts/results-storage.md` — "within `SCHEMA_VERSION = N.0`, we will add columns but never rename, drop, or narrow types." Iceberg as a future format alternative remains a roadmap item if rename/partition-evolution becomes load-bearing.

**Pre-1.0 work:** none required for runtime — it already enforces additive evolution. Worth documenting the commitment in `docs/concepts/results-storage.md` so early-adopter lakehouse consumers can plan around it.

### 2. HTTP API — SOFT contract pre-1.0

**The current state is leaky.** Most endpoints return `internal_pydantic.model_dump()` directly. Only one explicit `response_model=`. JSON shape mirrors `RunRow`/`StepRow` mirrors DuckDB silver mirrors parquet bronze. The four layers move together because nothing separates them.

That's not a problem pre-1.0 — early adopters accept JSON-shape churn between minor releases as long as we call out breaks in release notes. It *would* be a problem at 1.0 once we publish a stability commitment.

**0.x stability promise:**
- The endpoints currently exposed (`/api/runs`, `/api/runs/{id}`, etc.) keep working — no removals or renames in patch releases.
- Breaks in minor releases are called out in release notes with a one-line migration ("`field X` renamed to `Y` in 0.2.0").
- No path-versioning prefix yet — its absence signals "we haven't locked yet."

**Free evolution always:**
- Add new endpoints
- Add optional fields to existing response shapes (Pydantic ignores unknown fields by default; consumers tolerate)
- Add query parameters with sensible defaults
- Add response headers

**1.0 lock plan:**
- Add `/api/v1/...` path prefix at 1.0. The prefix appearing IS the lock signal — it tells consumers "JSON shapes within `/api/v1/...` are stable now." Pre-1.0 routes (`/api/foo`) ship as aliases for one minor release post-1.0 to soften the cut.
- Ship `response_model=` coverage as the locking mechanism: each endpoint declares an external Pydantic view class, routes return constructed views, internal models refactor freely behind them.
- Apply kubectl-style deprecation: when `/api/v2/...` eventually lands, `/api/v1/...` supported for ≥1 year.

**Pre-1.0 work that pays off both now and at 1.0:**
- **`response_model=` coverage on every endpoint.** Frame this as **OpenAPI quality work**, not as a stability lock. Doing it now improves the auto-generated `/openapi.json` immediately (clients can codegen against an accurate spec); it also pre-positions us for the 1.0 lock without requiring the lock today.

**Pre-1.0 work to defer:**
- The `/api/v1/` prefix itself. Adding it now would imply a stability commitment we're explicitly not making. Ship it at 1.0 as the locking ceremony.
- Stripe-style date-pinning. Too much infrastructure investment for our scale; reconsider post-1.0 if and when consumer count justifies it.

### 3. CLI (`litmus runs`, `litmus show`, `litmus serve`, etc.) — SOFT contract pre-1.0

**0.x stability promise:**
- Existing top-level command names keep working — no removals in patch releases.
- Existing flag names + semantics — no changes in patch releases.
- Breaks in minor releases are called out in release notes with one-line migrations.

**Free evolution always:**
- New flags with sensible defaults (always safe, like `gh`)
- New subcommands (additive)
- New output formats (`-f X`)
- New top-level commands
- Adding aliases (Docker `docker ps` ↔ `docker container ls` pattern — both work forever)

**1.0 lock plan:**
- Document the formal CLI stability policy in `docs/reference/cli.md` at the 1.0 cut: kubectl-style — GA flags supported ≥1 year or 2 releases (whichever longer) after deprecation; restructures use Docker-style aliasing; new flags safe with sensible defaults.
- No CLI version prefix (`litmus2 runs`) — git's "no major version since 2.0 in 2014" is the realistic target. The package version is the version.

**Pre-1.0 work to defer:**
- Writing the formal policy doc. Pre-1.0 the policy is implicitly "we may break in minor; release notes call it out." Writing the kubectl-style policy at 1.0 is the locking ceremony; doing it now overstates the commitment.

### 4. MCP tools — SOFT contract pre-1.0 (with extra care)

The "soft" classification has a caveat: tool names + descriptions are *part of the LLM prompt*. Renaming a tool mid-0.x doesn't just break consumer scripts; it breaks deployed agents whose system prompts reference the old name. So while it's technically a soft contract, the cost of breakage is higher than typical CLI/HTTP soft contracts.

**0.x stability promise:**
- Existing tool names and schemas are stable across patch releases.
- Breaks in minor releases get release notes + a deprecated-alias migration window (SEP-986 pattern: register the new name, keep the old as a deprecated alias).
- We pick names deliberately *the first time* to avoid renames.

**Free evolution always:**
- Add new tools
- Add optional input parameters with defaults
- Add new fields in output schemas (consumers tolerate unknown fields)

**1.0 lock plan:**
- Tool surface review (already on the `RELEASE-0.1.0.md` Tier 2 list) — "is this the tool surface I want forever?"
- Tool-naming convention written down: snake_case verbs, domain-scoped prefixes (`runs.list`, `runs.show`).
- No tool-level versioning yet — industry hasn't converged. Watch the MCP spec; adopt when consensus forms.

**Pre-1.0 work to do now:**
- The tool-surface review and naming convention. Renames hurt even pre-1.0 because deployed agents are harder to update than scripts. Better to invest in getting names right *before* early adopters wire agents around them than to alias-rename later.

**Pre-1.0 work to defer:**
- Per-tool versioning infrastructure. Industry hasn't picked a pattern; building our own locks us out of the eventual convention.

### 5. Event types in the WAL — HARD contract

The other hard pre-1.0 contract. Events are immutable on disk; renaming a field in code makes historical events from previous releases unreadable on the next start. Early adopters' running deployments would break silently. We hold this strictly even pre-1.0.

**0.x stability promise (apply Avro BACKWARD compatibility):**
- `EventBase` field set (`id`, `occurred_at`, `received_at`, `session_id`, `run_id`) doesn't change.
- Existing event class names and `event_type` discriminator strings don't change (`"session.started"`, `"measurement.recorded"`, etc.).
- Existing event field names + types don't change.

**Free evolution always:**
- Add new event types (consumers ignore unknown via tolerant-reader)
- Add optional fields to existing events (Pydantic `default=None`)
- Add new optional fields to `EventBase`

**What requires a real break (deferred):**
- Rename event types or fields (Greg Young's invariant: *"A new version of an event must be convertible from the old version. If not, it's not a new version, it's a new event."*)
- Change semantic meaning of a field
- Make existing optional fields required
- Remove fields from existing event types

If we ever need any of these, apply Axon-style upcasting middleware: a transformer between deserialization and the application reads old event shape and projects to new. Application code only sees latest; old events on disk stay readable. Likely a 1.0+ event.

**1.0 lock plan:** the additive-only commitment becomes published in `docs/concepts/event-log.md`. Upcasting infrastructure is built when the first reshape forces it.

**Pre-1.0 work:** none required for runtime — we already have weak-schema + tolerant-reader implicitly. Worth documenting the commitment explicitly so contributors know what's safe.

### 6. Python `analysis.*` (RunsQuery, StepsQuery, MeasurementsQuery)

**Already classified internal in `docs/audits/public-api.md:66-67`:**
> "`litmus.reports.*`, `litmus.grafana.*`, `litmus.analysis.*` — extension hooks for later releases; **treat as internal for 0.1.0**."

**What's locked at 0.1.0:** nothing externally. The HTTP/CLI/UI/MCP layers wrap these classes; users hit the wrappers, not the Python.

**Free evolution:** anything. Refactor field names, restructure classes, change method signatures. Constraint: in-tree consumers (the wrapping layers) update at the same time.

**Recommendation:** keep internal. The temptation to mark them public is real (they're well-shaped Pydantic models, look like a clean API), but the cost of locking them is high — they mirror DuckDB silver schema, so locking them locks silver, which couples back to bronze. The HTTP API view classes (Tier 1 above) become the external contract; analysis.* stays the implementation.

## Summary table

| Surface | Class | 0.x stability promise | Free evolution | 1.0 lock plan |
|---|---|---|---|---|
| Parquet artifact | HARD | Additive only within `SCHEMA_VERSION = "1.0"` | Add columns (auto); add `record_type` values (carefully) | Document the additive promise; bump `SCHEMA_VERSION` + ship migration tool when a real break hits |
| Event WAL | HARD | Additive only; class + field names stable | New event types; new optional fields | Document additive promise; build Axon-style upcasting when first reshape hits |
| HTTP API | SOFT | Existing endpoints work; minor breaks called out in release notes | Additive endpoints + fields | Add `/api/v1/...` prefix as locking ceremony; `response_model=` coverage; kubectl-style deprecation policy |
| CLI | SOFT | Existing commands + flags work; minor breaks in release notes | New flags + subcommands; aliases for restructures | Write formal CLI stability policy at the 1.0 cut |
| MCP tools | SOFT (with care) | Existing tools work; renames via deprecated-alias; pick names carefully *now* | New tools; new optional inputs/outputs | Tool-surface review + naming convention; no tool-level versioning until industry converges |
| Python `analysis.*` | INTERNAL | Nothing | Anything | N/A — stays internal |

## Recommended additions to `RELEASE-0.1.0.md`

These bucket the work by *when it lands*, framed against the 0.x → 1.0 path.

### 0.1.0 must-do (work that has to ship for early adopters to use Litmus safely)

- **Document the parquet additive-evolution promise** in `docs/concepts/results-storage.md`. One paragraph: "within `SCHEMA_VERSION = 1.0`, we will add columns but never rename, drop, or narrow types." Hard contract; consumers will plan around it.
- **Document the event WAL additive-evolution promise** in `docs/concepts/event-log.md`. Same shape: "event class names + fields + `event_type` strings stable; new event types + optional fields fine; renames require an upcasting story we don't have yet, so they're forbidden in 0.x." Hard contract.
- **Tool-surface review for MCP** with a written naming convention. Already on the Tier 2 list; promote because tool renames hurt agents disproportionately even pre-1.0.

### 0.x → 1.0 prep (work that pays off as quality NOW and as the locking surface LATER)

- **`response_model=` coverage on every FastAPI endpoint.** Frame as **OpenAPI quality work**, not stability lock. Doing it now produces a high-quality auto-generated `/openapi.json` that consumers can codegen against; it also pre-positions us for the 1.0 path-versioning lock without committing to it today. This is the biggest single lever — same work, three benefits (stability runway + better docs + client codegen).
- **Curate FastAPI app metadata** — explicit `title`, `version`, `description`, tagged endpoint groupings. Five-line config change; OpenAPI spec quality jumps immediately.
- **Release-note discipline** for any 0.x break — JSON-shape changes, CLI flag changes, MCP tool renames. Pre-1.0 the contract IS the release notes.

### At the 1.0 cut (the formal stability infrastructure)

- **Add `/api/v1/...` path prefix.** The prefix appearing IS the locking ceremony; pre-1.0 paths ship as aliases for one minor release post-1.0. This is the moment HTTP shapes lock.
- **Publish the formal CLI stability policy** in `docs/reference/cli.md` (kubectl-style: ≥1 year or 2 releases of deprecation warnings before removal; Docker-style aliasing for subcommand restructures).
- **Stamp the per-surface stability commitments as in-effect** — turn the "0.x promise" rows of the summary table into versioned guarantees.
- **Decide MCP tool-level versioning** if the industry has converged by then; otherwise hold the alias-on-rename pattern.

### Out of scope across the entire 0.x → 1.0 path

- **Date-based HTTP versioning (Stripe model).** Too much infrastructure investment for our scale; reconsider post-1.0 if and when consumer count justifies it.
- **Per-tool versioning for MCP.** Industry hasn't converged; building our own locks us out of the eventual convention.
- **Upcasting middleware for event evolution.** Build when the first real reshape forces it; likely never within 0.x.
- **Iceberg / Delta as the primary storage format.** Big architectural lift; parquet + `union_by_name=true` covers the additive case for the foreseeable future.

## Sources

The patterns and quotes in this document are drawn from:
- Stripe API versioning docs and the 2017 + 2024 Stripe blog posts on the date-pinning model
- GitHub's 2022 transition to date-header versioning
- Kubernetes Deprecation Policy (the only formal CLI policy found)
- Apache Iceberg, Delta Lake, and Apache Hudi schema evolution documentation
- Confluent Schema Registry compatibility levels (Avro convention)
- Phil Sturgeon, Zalando RESTful API Guidelines, Microsoft REST API Guidelines, Speakeasy
- Greg Young's "Versioning in an Event Sourced System"
- Axon Framework upcasting documentation
- The 2021 empirical event-sourcing schema-evolution study (arxiv 2104.01146)
- AWS CLI v1→v2 migration guide and lessons
- Docker CLI 1.13 subcommand restructuring
- MCP specification (`2025-11-25`) and SEP-986 tool-naming proposal
- Semver 2.0.0 spec, Cargo book, Effective Rust on pre-1.0 conventions

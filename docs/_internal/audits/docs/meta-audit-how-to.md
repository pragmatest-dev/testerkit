# Meta-audit: How-to section
**Date:** 2026-05-17
**Scope:** 16 pages

## Severity totals
| Page | ❌ | ⚠️ | 💡 |
|---|---|---|---|
| writing-tests | 7 | 17 | 14 |
| limits | 5 | 12 | 9 |
| mock-mode | 8 | 17 | 9 |
| configuring-stations | 6 | 21 | 15 |
| custom-drivers | 14 | 17 | 16 |
| vector-expansion | 7 | 15 | 12 |
| spec-driven-testing | 4 | 15 | 15 |
| profiles | 4 | 16 | 15 |
| traceability | 7 | 18 | 17 |
| managing-sessions | 4 | 16 | 12 |
| querying-events | 2 | 12 | 11 |
| querying-channels | 5 | 13 | 15 |
| mcp-integration | 9 | 19 | 18 |
| context-architecture | 8 | 20 | 13 |
| multi-dut-testing | 6 | 18 | 16 |
| grafana-dashboards | 3 | 11 | 15 |
| **Total** | **99** | **257** | **222** |

99 CRITICAL — by far the worst section. `custom-drivers.md` alone has 14 criticals; `mcp-integration.md` has 9; `mock-mode.md` and `context-architecture.md` have 8 each.

---

## Auditor accuracy check (source-verified)

### ✅ Confirmed correct — the big ones

**`litmus_sweeps(vin=[...])` kwargs form raises `pytest.UsageError` at collection.**
`src/litmus/pytest_plugin/markers.py:126-130`:
```python
if kwargs:
    raise ValueError(
        f"{name} does not accept keyword arguments; pass a list of "
        "entries as one positional argument or varargs."
    )
```
Called from `src/litmus/pytest_plugin/hooks.py:1491-1495` which wraps in `pytest.UsageError`. Same rejection applies to `litmus_mocks` and `litmus_characteristics` (shared `normalize_inline_list_payload`). Only `litmus_limits` accepts kwargs.

**This means `vector-expansion.md` has 14+ broken examples** that teach the kwargs form. The same antipattern likely appears across other how-to pages, tutorials, and reference pages. This is the new "Bug E" cross-cutting issue.

**HTTP channel query params are `?since=` / `?until=`, NOT `?start=` / `?end=`.**
`src/litmus/api/app.py:512-513` — `since: str | None = None, until: str | None = None`. The `querying-channels.md` and `reference/api.md:259` both use the wrong names.

**`ChannelStore.__init__` appends `channels/` to data_dir.**
`src/litmus/data/channels/store.py:198` — `self._channels_dir = data_dir / "channels"`. Documentation example `ChannelStore(Path("results/channels"))` would write to `results/channels/channels/`.

**`_ensure_connected()` does NOT exist on `Instrument` or `VisaInstrument`.**
`grep -rn "_ensure_connected" src/litmus/instruments/` → 0 hits. The 8 places in `custom-drivers.md` that call it would all raise `AttributeError`.

**Event log filenames include `-{pid}` segment.**
`src/litmus/data/event_log.py:178` — `path=date_dir / f"{session_id}-{os.getpid()}.arrow"`. Pages depicting `{session_id}.arrow` are wrong.

**`litmus data prune` is shipped, not "planned".**
`src/litmus/cli.py:2358` — `@data.command("prune")`. `managing-sessions.md` calls it a planned command.

**`logger.measure()` does NOT accept `dut_pin`, `instrument_name`, `instrument_channel` kwargs.**
`src/litmus/execution/logger.py:941-948` — signature is `(name, value, *, limit, outcome, allow_repeat)`. `traceability.md:104-115` shows these phantom kwargs.

**`pytest-mock` is NOT a Litmus dependency.**
Not in `pyproject.toml`. `mocks.py` uses `unittest.mock.patch.object` directly. Multiple pages credit `pytest-mock`.

---

## Cross-page patterns

### Pattern E (NEW — biggest find): `litmus_sweeps(vin=[...])` kwargs form is rejected at collection
The page-by-page audits confirm this is a runtime-breaking error wherever it appears. Canonical form is `litmus_sweeps([{"vin": [...]}])` (list of axis-group dicts). Same shape required for `litmus_mocks` and `litmus_characteristics`.

Auditor flagged this on `vector-expansion.md` and `reference/litmus-markers.md`. **Likely also present in tutorial pages we already audited** — needs grep sweep across all docs.

### Pattern F: Wrong `logger.measure` kwargs (cross-section, NOT just tutorial)
Same tutorial-section Bug A appears in how-to:
- `writing-tests.md`: `units=`, `low=`, `high=` shown
- `limits.md`: cascade rung 1 names same wrong kwargs
- `traceability.md`: `dut_pin=`, `instrument_name=`, `instrument_channel=` — additional phantom kwargs from a different model's API
- `mock-mode.md` and others: implied by chain

### Pattern G: Wrong HTTP channel query parameters
`?start=` / `?end=` shown in:
- `querying-channels.md` (L34, L86)
- `reference/api.md` (L259)

Actual API uses `?since=` / `?until=`. Curl examples silently return all data because the filter params are ignored.

### Pattern H: ChannelStore constructor misuse
`querying-channels.md` shows `ChannelStore(Path("results/channels"))` — but the constructor appends `channels/`, so the example queries `results/channels/channels/`. Empty result, silent.

### Pattern I: Phantom methods on instrument base classes
- `_ensure_connected()` (custom-drivers.md, 8 sites)
- `_sim_responses` dict shape (custom-drivers.md) — doesn't match `_generate_sim_config` behavior

### Pattern J: Required Pydantic fields silently omitted in YAML examples
- `multi-dut-testing.md`: FixtureConnection `name:` field missing in YAML → `Field required` validation error
- `configuring-stations.md`: `StationConfig.name:` marked optional in doc, actually required
- `writing-tests.md`: examples that would fail validation

### Pattern K: Phantom prerequisites
A how-to assumes the reader has a station YAML, product YAML, fixture YAML, and/or running Grafana / litmus serve / MCP server — but doesn't say so. Pages affected: most of them. Worst: querying-events (litmus serve), querying-channels (litmus serve + data_dir resolution), grafana-dashboards (Grafana itself), multi-dut-testing (station with named roles), traceability (4 YAML files), spec-driven-testing (chamber/eload fixtures that don't exist).

### Pattern L: How-to written as Concepts/Explanation
- `context-architecture.md` reads as explanation, not task — most of the ContextVar/StashKey content belongs on a concepts page
- `custom-drivers.md` blends architecture discussion with code examples without a clear task arc

### Pattern M: `MissingLimitError` never explained
`verify` raises `MissingLimitError` when no limit resolves. Pages teaching verify never mention this. Pages: limits.md, writing-tests.md, spec-driven-testing.md, traceability.md.

### Pattern N: Cold first-uses of `pins`, `verify`, `logger`, `context`, `sync`
Repeat of the tutorial pattern. Pages affected: nearly all how-to pages. `multi-dut-testing.md` cold-drops `sync` and `InstrumentServer` in the lead.

### Pattern O: Wrong recommendations for the canonical approach
- `mock-mode.md`: "Mock Value Priority" merges 3 independent mock pipelines into one fictional chain
- `limits.md`: claims `verify` "bypasses" the limit chain — it doesn't; same chain as `logger.measure`
- `writing-tests.md`: shows `examples/03-profiles/conftest.py` which doesn't exist (real path: `examples/07-profiles/`)
- `profiles.md`: claims bare `pytest` defaults to baseline — actually raises an error when profiles are declared without `default_profile:`

### Pattern P: HTTP / MCP / CLI surface mismatches
- `mcp-integration.md`: `litmus_run` return shape documented wrong (page lists fields that aren't there, omits ones that are); `litmus_project(action="save", type="test")` is broken (`.yaml.py` filename bug)
- `grafana-dashboards.md`: `--refresh` vs `--refresh-seconds` (inconsistent within same page)
- `managing-sessions.md`: `results/events/` path prefix doesn't exist; `<data_dir>/events/` is canonical

---

## Severity-distribution insights

- **`custom-drivers.md` (14 critical)** is the most broken page. Multiple code examples don't run as written. This page needs to be rebuilt against the actual `Instrument` / `VisaInstrument` API.
- **`mcp-integration.md` (9 critical)** — the MCP surface contract drifted significantly without the docs catching up.
- **`mock-mode.md` (8 critical)** — the "Mock Value Priority" claim is structurally wrong; this is the page operators trust.
- **`context-architecture.md` (8 critical)** — self-contradicts on whether context is mutable; teaches a context-vs-parquet model that doesn't match the pytest path (same Bug C from tutorial section).

---

## Recommended fix order (after Reference + Integration audited)

**Cross-cutting sweeps (do these first):**

1. **Bug E — `litmus_sweeps`/`mocks`/`characteristics` kwargs form** → list-of-dicts form everywhere. Grep `litmus_sweeps(` followed by `=[` in all docs.
2. **Bug F — `logger.measure` kwargs** → already in tutorial sweep; extends to how-to.
3. **Bug G — `?start=`/`?end=` → `?since=`/`?until=`** for HTTP channel queries.
4. **`results/events/` and `results/runs/` prefixes** → drop the `results/` prefix; use `<data_dir>/events/`, `<data_dir>/runs/`.

**Per-page fixes:**

5. `custom-drivers.md` — rewrite the broken examples against real APIs
6. `mcp-integration.md` — verify every documented tool return shape against `src/litmus/mcp/tools.py`
7. `mock-mode.md` — rewrite "Mock Value Priority" to match the actual 3 pipelines
8. `context-architecture.md` — resolve mutability self-contradiction, fix the parquet-stamping claim
9. Add `MissingLimitError` mention to limits.md, writing-tests.md, spec-driven-testing.md, traceability.md
10. Add prerequisites blocks to every how-to that needs YAMLs / running servers

---

## Consistency notes (cross-page)

- **Limit cascade ordering**: limits.md says one thing, writing-tests.md another; both differ from actual code which has two distinct paths (`_resolve_measurement_limit` per-measurement vs `_litmus_push_limits` at collection)
- **Mock pipelines**: 3 independent ones (autouse markers, harness fallback, InstrumentPool station config) — never explained as 3 in one place
- **`--refresh` vs `--refresh-seconds`** within same Grafana page
- **`results/` prefix**: used inconsistently across docs; the real layout is `<data_dir>/{events,runs,channels}/` with no `results/` parent
- **`pytest-mock` credited as Litmus support** in why-pytest.md (concepts) and contradicted by `mocks.py`

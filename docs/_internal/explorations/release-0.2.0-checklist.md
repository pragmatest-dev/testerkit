# Release checklist — v0.2.0

Data-architecture release (FileStore + the `observe`/`verify`/`stream`
verbs + Part/UUT rename). Working tracker for the tag. Outward-facing
steps stay unchecked until explicitly approved.

## Done (committed on `feat/0.2.0-data-improvements`)

- [x] `product → part` / `dut → uut` rename across the codebase (#258)
- [x] ATML exporter dropped; JSON/HDF5 exporter conformance bugs fixed
- [x] `event_binding`: channel-data callbacks marshal onto the UI loop;
      `interactive_station.py` aligned to NiceGUI best practices
- [x] Channel chart: legend under the plot (scroll), `LiveBadge`
      (activity-driven idle/live), Time | Index x-axis toggle (URL-shared)
- [x] `litmus_files` MCP tool + `GET /api/files/catalog` HTTP parity
- [x] Skills: teach `observe`/`stream`; `refs/observe.md`; completed the
      generated-CLAUDE.md MCP tool list
- [x] `.gitignore` `*.log`

## Release-blocking — must land before tag

- [ ] **Grafana dashboards stale on the channel schema.** `channel_explorer.json`
      queries `timestamp` / `samples` (renamed to `received_at` / `value`);
      sweep the 10 dashboards for old column + `product_*`/`dut_*` names.
- [ ] **Regenerate demo / example data** so no pre-rename `product_*`
      parquet lingers (showed up in the /explore X-axis dropdown).
- [ ] Version bump `0.1.3 → 0.2.0` in `pyproject.toml` + `uv.lock`
- [ ] CHANGELOG: finalize `[0.2.0]` (done, uncommitted); add `litmus_files`
      + the skill updates to the Added section; commit
- [ ] Full green gate: `ruff check`, `pyright`, `pytest` (whole suite)

## AI-surface currency (found during release audit)

- [x] MCP clean of `product`/`dut`; FileStore now reachable (`litmus_files`)
- [~] "When to use `stream`" — covered in `refs/observe.md` (decision
      table + sweep/soak guidance); sharpen observe(array) vs stream(sample)
      vs sink if it reads thin
- [ ] **Channels vs files in interactive UIs** — no agent guidance for
      building live UIs (when to push to ChannelStore for live fan-out vs
      FileStore for artifacts). Concept docs exist (`three-verbs.md`,
      `capture-an-artifact.md`, tutorials 10/12) but no skill ties it to
      UI building.

## Outward-facing — explicit go required (irreversible)

- [ ] Merge `feat/0.2.0-data-improvements → main` (~205 commits ahead)
- [ ] Tag `v0.2.0`
- [ ] Publish to PyPI (`litmus-test`)

## Deferred to post-0.2.0

- [ ] Index-mode chart: decimate per-session so the overlay isn't sparse
- [ ] String / complex channel renderers + a per-type "view as…" selector

# How-To — Data

Pull data out of the runtime. Query the event log and channel store, debug failures, compare runs, export to interchange formats, dashboard via Grafana.

- [Querying historical events](querying-events.md) — MCP tool, HTTP API, Python
- [Querying channel data](querying-channels.md) — time-series data plane
- [Capture a waveform and judge derived scalars](capture-waveform.md) — observe + verify pattern for scope traces
- [Stream a live channel](stream-live-channel.md) — channels.stream from interactive code; live UI updates push-style
- [Capture an artifact](capture-an-artifact.md) — observe(PIL.Image / bytes / Pydantic) + files.stream byte-stream sinks
- [Find flaky tests](find-flaky-tests.md) — use Metrics → Retest and Results detail to spot intermittents
- [Compare two runs](compare-runs.md) — diff known-good vs failing with two tabs + a DuckDB query
- [Export results](export-results.md) — `litmus show -f` for reports (HTML/PDF/JSON/CSV) and `litmus export -f` for interchange (STDF/HDF5/TDMS/MDF4/ATML)
- [Query runs and metrics via MCP](mcp-query-runs.md) — `litmus_runs` / `litmus_steps` / `litmus_metrics` recipes
- [Debug failures via MCP](mcp-debug-failures.md) — chained investigative workflow when a run fails
- [Grafana dashboards](grafana-dashboards.md) — pre-built dashboards for results, events, channels

## See also

- [Concepts → Data](../../concepts/data/index.md) — event log, three stores, sessions, flight streaming
- [Reference → Event types](../../reference/data/event-types.md), [Parquet schema](../../reference/data/parquet-schema.md), [Query API](../../reference/data/query-api.md)
- [Integration → Grafana](../../integration/data/grafana.md) — the pgwire data source and shipped dashboards

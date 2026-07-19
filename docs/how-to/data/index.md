# How-To — Data

Pull data out of the runtime. Query the event log and channel store, debug failures, compare runs, export to interchange formats, dashboard via Grafana.

- [Querying historical events](querying-events.md) — MCP tool, HTTP API, Python
- [Querying channel data](querying-channels.md) — time-series instrument data
- [Choosing a channel verb](choosing-a-channel-verb.md) — write/stream vs latest/live/query, picked by cadence and intent
- [Capture a waveform and judge derived scalars](capture-waveform.md) — observe + verify pattern for scope traces
- [Stream a live channel](stream-live-channel.md) — channels.stream from interactive code; the UI updates live as samples land
- [Capture an artifact](capture-an-artifact.md) — observe(image / bytes / Pydantic record) + files.stream for byte streams
- [Find flaky tests](find-flaky-tests.md) — use Metrics → Retest and Results detail to spot intermittents
- [Compare two runs](compare-runs.md) — diff known-good vs failing with two tabs + a DuckDB query
- [Export results](export-results.md) — `testerkit show -f` for reports (HTML/PDF/JSON/CSV) and `testerkit export -f` for interchange (STDF/HDF5/TDMS/MDF4)
- [Query runs and metrics via MCP](mcp-query-runs.md) — `testerkit_runs` / `testerkit_steps` / `testerkit_metrics` recipes
- [Debug failures via MCP](mcp-debug-failures.md) — chained investigative workflow when a run fails
- [Grafana dashboards](grafana-dashboards.md) — pre-built dashboards for results, events, channels
- [Benchmark your machine](benchmarking.md) — `testerkit benchmark` measures per-store throughput and writes a sendable result file

## See also

- [Concepts → Data](../../concepts/data/index.md) — event log, data stores, sessions, flight streaming
- [Reference → Event types](../../reference/data/event-types.md), [Parquet schema](../../reference/data/parquet-schema.md), [Query API](../../reference/data/query-api.md)
- [Integration → Grafana](../../integration/data/grafana.md) — the pgwire data source and shipped dashboards

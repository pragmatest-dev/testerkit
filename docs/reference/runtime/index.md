# Reference — Runtime

The interactive and programmatic surfaces — for LabVIEW, TestStand, scripts, dashboards, AI agents. Where the pytest path is the bench-default, the runtime surfaces are the everywhere-else path.

- [`LitmusClient`](client.md) — Python client that submits test runs (no pytest required). Suits LabVIEW / TestStand bridges.
- [`connect()`](connect.md) — interactive instrument access for scripts, notebooks, the operator UI. Returns a `StationConnection` with the full event-log / channel-store / instrument-pool surface.
- [HTTP & MCP API](api.md) — REST endpoints exposed by `litmus serve`, plus the twelve MCP tools. Same shapes either way. Generated from source.

## See also

- [Integration](../../integration/) — runner-side bridges for non-pytest entry points (OpenHTF, LabVIEW, TestStand, plain scripts)
- [How-to → MCP integration](../../how-to/overview/mcp-integration.md) — register the MCP server with each supported AI client
- [How-to → Query runs and metrics via MCP](../../how-to/data/mcp-query-runs.md) — worked recipes for the data-side MCP tools

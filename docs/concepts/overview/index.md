# Concepts — Overview

The platform's mental model from above. Read in order if you're new; jump to the specific page if you're chasing a particular framing.

- [Architecture](architecture.md) — system-level view of parts, stations, fixtures, and runs, with the pytest plugin in the middle
- [Platform vs framework](platform-vs-framework.md) — Litmus owns infrastructure (configuration, data, instrumentation, AI / operator surface); pytest owns test execution
- [pytest](pytest.md) — why the platform rides on pytest as the default runner instead of its own
- [AI integration](ai-integration.md) — the MCP server, what tools an AI agent gets, and where the platform draws the line between "platform exposes" and "platform calls an LLM"

## See also

- [Configuration](../configuration/index.md) — the YAML entities the architecture refers to
- [Data](../data/index.md) — where runs land after execution

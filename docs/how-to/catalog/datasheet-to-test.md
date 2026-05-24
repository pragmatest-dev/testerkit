# Datasheet → tests with Claude Code

Walks through the `datasheet-to-test` workflow end-to-end: from a product datasheet PDF to a runnable pytest suite, with operator approval at every step. This is the highest-leverage AI-assisted flow Litmus ships — it covers spec extraction, instrument selection, station config, and test scaffolding in one chained conversation.

For motivation see [why AI integration](../../concepts/overview/ai-integration.md). For the full inventory of what ships, see the [skills reference](../../reference/overview/skills.md).

## Prerequisites

- Litmus installed (`pip install litmus-test`)
- A product datasheet PDF on disk
- Claude Code installed and authenticated, with access to a high-capability model on your plan (Opus, GPT-5 / o-series, Gemini 2.5 Pro, or equivalent — the workflow does heavy PDF extraction)
- A working catalog of instruments — either real catalog YAMLs in your project, or you can use Litmus's bundled generics

If you don't have Claude Code, swap to any client that supports MCP — the steps work the same, only the invocation differs (slash command vs conversational). See the [skills reference](../../reference/overview/skills.md#slash-commands) for the matrix.

## One-time setup

Register Litmus's MCP server with Claude Code:

```bash
litmus setup claude-code
```

This does three things ([full reference](../../reference/overview/skills.md#what-the-setup-commands-install)):

1. Registers Litmus as an MCP server (`claude mcp add litmus -- <litmus-bin> mcp serve`)
2. Copies `/catalog-from-datasheet` and `/process-catalog` slash command stubs into `./.claude/commands/`
3. Writes or merges `./CLAUDE.md` with the Litmus project instructions

Restart Claude Code after `litmus setup` so it picks up the new server.

Confirm MCP registration:

```bash
claude mcp list
```

Litmus should appear in the list. If it doesn't, re-run `litmus setup claude-code` and check the printed error.

## Invoke the workflow

Open Claude Code in your project directory. Start a new conversation with this prompt:

```
Run the datasheet-to-test workflow on this datasheet:
./datasheets/my_part.pdf
```

Claude fetches the workflow prompt via MCP (`prompts/get datasheet-to-test`) and begins **Phase 1**.

## The six phases

The workflow STOPS at every gate. You approve, edit, or reject before it moves on.

### Phase 1 — Parse datasheet

Claude reads the PDF, extracts:

- Pin definitions (names, types, signal directions)
- Electrical characteristics (voltage ranges, current draws, accuracy specs)
- Test conditions (temperature, supply voltage points)

You see a structured summary. Approval gate: "Here's what I extracted, anything missing or wrong?"

Common edits at this gate:
- Renaming pins to match your team's convention
- Adding characteristics the datasheet implies but doesn't tabulate (e.g., quiescent current at no-load)
- Removing characteristics you don't intend to test

### Phase 2 — Save product spec

Claude saves the spec to `products/<part_number>.yaml`. The spec uses the [Capability schema](../../reference/catalog/schema.md) — same `signals`/`conditions`/`controls`/`attributes` shape as catalog entries. The MCP `litmus_project(action="save", type="product", ...)` call validates the YAML server-side against the live Pydantic model; you'll see Claude correct shape errors in-flight if it tries to save something invalid.

Approval gate: review the saved YAML. Edit directly if you want — the agent re-reads on the next step.

### Phase 2b — Recommend instruments

Claude calls `litmus_match(product_id=<id>)`. The platform derives capability requirements from the saved characteristics automatically (you don't build the requirements manually). Three outcomes:

| Outcome | What Claude proposes |
|---|---|
| Catalog has matches | Shortlist of matched catalog entries with capability coverage |
| Catalog is empty / no matches | Asks what equipment you have available |
| Mixed | Catalog matches for some, asks about gaps |

If you have a specific model the catalog doesn't know yet, Claude offers three paths:

1. `/catalog-scaffold <model>` — fast, from the model's prior knowledge
2. `generic_dmm` / `generic_psu` / `generic_eload` / `generic_oscilloscope` — bundled with Litmus, approximate capabilities, fine for mocked development
3. `/catalog-from-datasheet <pdf>` — slow, accurate, for production work where spec correctness matters

Pick whichever fits where you are: generics if you're sketching, scaffold for well-known instruments, full datasheet for the actual production catalog entry.

### Phase 3 — Create station config

Claude generates `stations/<id>.yaml` wiring the selected instruments to roles (`psu`, `dmm`, `dut_load`, etc.) with realistic mock values. Schema is validated server-side.

Approval gate: review the wiring. Most edits here are around VISA resource strings (the agent has no way to discover your bench's actual `TCPIP::*::INSTR` addresses unless you've already populated them or it can call `litmus_discover` against a live bench).

### Phase 4 — Generate tests

Two files generated:

- `tests/test_<product>.py` — pytest-native test code using the Litmus fixtures (`context`, `verify`, `logger`, plus instrument role fixtures like `psu`, `dmm`)
- `tests/test_<product>.yaml` — sidecar YAML with `vectors:`, `limits:`, `mocks:` for operator-editable values

The generated tests use:
- `verify(name, value)` for judgment-bearing measurements — limits resolve from the active product spec / sidecar / profile, raises on out-of-band
- `logger.measure(name, value)` for record-only setup readouts
- `context.changed("vin")` in parametrized sweeps to skip expensive reconfig
- Native `@pytest.mark.parametrize` for code-owned sweeps; sidecar `vectors:` for operator-edited sweeps

Approval gate: review both files. Edit directly — pytest and the YAML are the source of truth, not Claude's memory of what it generated.

### Phase 5 — Execute and analyze

Claude calls `litmus_run(test="tests/test_<id>.py", station="<station_id>", serial="<DUT-SERIAL>", project=<root>)`. The test executes against the configured station (with `mocks:` from the sidecar if `--mock-instruments` is implied, otherwise against the real bench).

Claude shows you the results table — rows are measurements, columns are sweep axes, cells colored pass/fail/skip. From here you can ask follow-ups:

- "Why did the load=0.8 row fail at vin=4.5?" → Claude pulls the measurement detail via `litmus_runs` + `litmus_steps`
- "Open this run in the browser" → `litmus_open(type="run", id=<run-id>)` returns a `litmus serve` URL
- "Tighten the output_voltage tolerance to ±1%" → Claude edits the sidecar and re-runs

## What's actually saved to your project

After the workflow completes, your project tree has:

```
my_project/
├── litmus.yaml                          # set by litmus_project init
├── products/
│   └── <part_number>.yaml               # phase 2
├── stations/
│   └── <station_id>.yaml                # phase 3
├── tests/
│   ├── test_<product>.py                # phase 4
│   └── test_<product>.yaml              # phase 4 sidecar
└── catalog/                             # only if /catalog-scaffold or similar ran
    └── <instrument>.yaml
```

Every file is plain YAML or Python. Git diffs work. Code review works. If you want to delete the AI from the loop, `pytest tests/` still runs the test.

## When things go wrong

| Symptom | Cause | Fix |
|---|---|---|
| Claude can't find the workflow | MCP server not registered or Claude not restarted after `litmus setup` | `claude mcp list` to verify; restart Claude Code |
| Agent loops trying to save an invalid YAML | The Pydantic model rejected the shape it tried to save | Let it iterate — server-side validation is the feedback signal; or stop it and edit the YAML by hand |
| Test runs but every row is `SKIP` / `MISSING_LIMIT` | Phase 4 generated `verify()` calls without limits, and no sidecar / product spec covers them | Either add limits to the sidecar `limits:` block or switch the calls to `logger.measure()` for characterization-only |
| Instrument match returns nothing | Catalog is empty or capability requirements aren't covered | Use generics for development, or run `/catalog-from-datasheet` for the missing instruments |
| Agent picks the wrong instrument | The capability match is correct but the agent's preference doesn't match yours | Tell it directly: "I want the Keithley 2400 for the load instead of the eload" — it'll update the station and re-validate |

## Variations

Three ways to use this flow short of the full pipeline:

1. **Just product spec.** Stop after Phase 2. You get a product YAML; do station + tests by hand.
2. **Product + tests, mock instruments.** Stop after Phase 4, use the bundled `generic_*` instruments through Phase 2b, run `pytest --mock-instruments`. No real hardware needed; useful for test-code review before the bench is ready.
3. **Incremental update.** Re-invoke the workflow on a product that already has files. The agent reads the existing YAMLs and proposes diffs rather than from-scratch generation.

## See also

- [Concepts: why AI integration](../../concepts/overview/ai-integration.md) — motivation
- [Reference: skills](../../reference/overview/skills.md) — full inventory of workflows, agents, MCP tools
- [How-to: MCP integration](../overview/mcp-integration.md) — per-client setup detail
- [Reference: catalog schema](../../reference/catalog/schema.md) — the shape product spec and catalog entries share
- [Tutorial step 3: pytest-native tests](../../tutorial/03-fixtures.md) — what the generated test code looks like in context

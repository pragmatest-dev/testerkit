# Tiers — Start Simple, Grow When You Need To

`pytest` passes right after `pip install testerkit` — no server, no account, no station YAML, no hardware. TesterKit adds configuration layers (station identity, part specs, profiles) only when you reach for them; nothing is required up front. A project can sit at the same low tier for its whole life and that's a correct outcome, not an unfinished one.

The layers form a ladder. Each rung adds one thing without touching the layer below it — a test written at Tier 0 keeps its body unchanged all the way to Tier 3.

## The rungs

| Tier | Scaffold | What it adds |
|------|----------|---------------|
| **0 — bringup, inline** | `testerkit init <name> --tier bringup` | A `conftest.py` with `unittest.mock.MagicMock` instrument fixtures you write by hand, one smoke test, no YAML at all. Limits live inline as a dict literal passed to `verify`. |
| **1 — bringup, sidecar** | same scaffold as Tier 0 | A same-named `<test_file>.yaml` next to the test carries the limits (and sweeps / mocks / retry, if you add them) instead of the inline dict. The test body doesn't change — only where the limit value lives. |
| **2 — bench** | `testerkit init <name> --tier bench` (equivalent to `--starter`) | Station, part, and fixture YAML. Real driver classes resolve through the [instrument catalog](../configuration/capabilities.md) instead of hand-written fixtures. Run with `--mock-instruments` to swap every station instrument for a mock populated from that instrument's `mock_config:` block — hardware-free CI on the same station config that runs on the bench. |
| **3 — factory** | `testerkit init <name> --tier factory` (bench scaffold + profile skeletons) | Named [profiles](../../how-to/execution/profiles.md) under `profiles/*.yaml`, each declaring a facet combination. `pytest --test-phase=<facet>` picks one profile for a run, binding its limits, mocks, and (optionally) a required station type in one flag. |
| **4 — grows out of 3** | no separate `--tier` value; adopted piece by piece on top of the factory scaffold | Multiple UUTs on one station ([sites](../configuration/fixtures.md#multi-uut-scaling-sites-shared-instruments-switching)), retest-rate analytics (`testerkit metrics retest`), characterization profiles that record without judging, lakehouse export. |

Tier 0 and Tier 1 share one scaffold — the only thing that moves is where the limit value lives (inline vs. sidecar). Tier 4 isn't a separate `testerkit init` choice either: it's the set of things a Tier 3 project reaches for next, added independently as each becomes a real need.

## Two different kinds of "mock"

Tier 0/1 and Tier 2+ swap out hardware differently, and the difference matters once you're debugging a fixture:

- **Tier 0/1** — you write the `conftest.py` fixture yourself with a plain `unittest.mock.MagicMock()`. There's no station YAML to read a `mock_config:` from.
- **Tier 2+** — the station YAML declares each instrument's `mock_config:` (its canned return values), and `pytest --mock-instruments` substitutes a mock built from that config for every instrument the station declares. The same station file — and the same `mock_config:` block — backs both the mock run and the real bench run.

## When to stop

A project with five tests on Tier 1 is done. Don't add `parts/` until a test actually wants a `tolerance_pct` override sourced from a part spec instead of a literal `low`/`high`. Don't add `profiles/` until there's a real recurring split — validation vs. production, or a second part variant — that a single sidecar can't express. Graduating a tier is something you choose because the layer below started to hurt, never something TesterKit requires to keep running.

## See also

**Same topic, other quadrants:**

- [Tutorial → Step 1: Run Something](../../tutorial/01-first-test.md) — the Tier 0 scaffold, hands-on
- [Tutorial → Step 2: Running Without Hardware](../../tutorial/02-mock-instruments.md) — `--mock-instruments` and `mock_config`
- [Tutorial → Step 5: Test Configuration](../../tutorial/05-configuration.md) — the Tier 1 sidecar
- [Tutorial → Step 6: Part Specifications](../../tutorial/06-specifications.md) and [Step 7: Real Instruments](../../tutorial/07-real-instruments.md) — the Tier 2 layers
- [Tutorial → Step 13: Parallel Testing](../../tutorial/13-parallel-testing.md) — multi-UUT sites, a Tier 4 topic
- [How-to → Profiles](../../how-to/execution/profiles.md) — writing and selecting Tier 3 profiles
- Run `testerkit refs show tiers` for the same ladder in terse, agent-oriented form (also `testerkit refs show routing` for choosing a verb + rung, and `testerkit refs show profiles` for the profile shape)

**Sibling concepts:**

- [Architecture](architecture.md) — how parts, stations, fixtures, and the pytest plugin fit together once you're past Tier 1
- [Platform vs framework](platform-vs-framework.md) — why the layers are infrastructure TesterKit owns, not test-execution mechanics
- [Fixtures](../configuration/fixtures.md) — the Tier 2 fixture layer, including the Tier 4 multi-site case

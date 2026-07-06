# Stage 8 — Waveform evidence

The canonical `observe` + `verify` pattern: capture the raw waveform that
backs a verified scalar measurement, so the analyst can navigate from a
pass/fail row in `/results/{run_id}` to the supporting trace with one click.

## What's in here

- **`drivers/scope.py`** — a Scope class plus `synthesize_psu_step_response()`,
  a generator that returns a fresh `Waveform` per call with realistic shape
  (5 V step, ~5 µs rise, ~3 % overshoot, ~30 µs settling) and small per-call
  jitter so derived measurements vary between captures.
- **`drivers/psu.py`** — minimal PSU interface.
- **`conftest.py`** — fixtures wire the mock Scope's `capture` method to the
  synthesizer callable, so `scope.capture()` produces a real Waveform.
- **`tests/test_psu_step_response.py`** — one test that captures the step,
  observes the waveform into ChannelStore, derives `rise_time_us` and
  `overshoot_v`, and verifies each against a limit.

## Why this pattern

A scalar `verify` is the judgment; the waveform is the evidence behind it.
Calling `observe("scope_step", wf)` before the two `verify`s does two things:

1. Routes the waveform to ChannelStore (typed array row + `sample_interval`),
   returning a `channel://scope_step?session=…` URI.
2. Stamps that URI as the `scope_step` output on every measurement row in
   this vector — so the two verify rows (`rise_time_us`, `overshoot_v`) both
   carry the URI of the waveform they were derived from.

On `/results/{run_id}`, the measurement rows show the `scope_step` output as a
clickable link to the waveform in `/channels/scope_step`. Failing a limit
takes you one click from "what" to "why."

The Waveform routes to ChannelStore (not FileStore) because it has a typed
array of samples plus a `sample_interval` — exactly what ChannelStore's
typed-row schema is for. Plain blobs (PIL images, vendor binaries, PDFs)
route to FileStore instead; that's example 10.

## Run it

```bash
cd examples/08-waveform-evidence
uv run pytest -v
```

Then open the operator UI to see the result:

```bash
uv run litmus serve --reload
```

- `http://localhost:8000/results` — the run, with both verify rows passing
- Click into the run → both rows show the `scope_step` output as a `channel://` URI
- Click the URI → `/channels/scope_step` plots the captured waveform

Each pytest invocation produces a new run with a slightly different rise
time and overshoot value, but both stay inside the limits.

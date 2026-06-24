# Pause a test for operator input

To pause a test and ask the operator to confirm a step, pick an option, or type a value, declare the prompts with the `litmus_prompts` marker and read each response by calling the `prompt` fixture. The mechanism is small — one marker, three prompt types, one fixture call. The example below shows the patterns; the design rules later help you write prompts an operator can act on with confidence.

## The mechanism in 30 seconds

```python
import pytest

@pytest.mark.litmus_prompts(
    insert_uut={"message": "Insert UUT, then click Confirm.", "prompt_type": "confirm"},
    pick_bench={"message": "Which bench?", "prompt_type": "choice",
                "choices": ["bench_01", "bench_02"]},
    chamber_temp={"message": "Set chamber temperature (°C):", "prompt_type": "input"},
)
def test_setup(prompt):
    prompt("insert_uut")                # waits for the operator to click Confirm
    bench = prompt("pick_bench")        # returns the selected choice (str)
    temp = prompt("chamber_temp")       # returns the typed input (str)
```

Markers can land file-level, class-scoped, or per-test (more
specific wins). Routing of the prompt itself is automatic:

1. If the operator UI is running, the prompt becomes a dialog in
   the browser (and lights up the amber **ACTIVE TESTS** sidebar
   block on every UI page).
2. If `LITMUS_AUTO_CONFIRM=1` is set, it auto-resolves for CI /
   smoke runs.
3. If stdin is a tty, it falls back to a terminal prompt.
4. Otherwise the test raises `PromptUnavailableError`.

## The three prompt types

| `prompt_type` | What it asks | Return value |
|---|---|---|
| `confirm` | Single OK / acknowledge action — "did you do this?" | `True` once acknowledged |
| `choice` | Pick one option from a fixed list | The selected string |
| `input` | Free-text field — "what value did you set?" | The typed string |

`timeout_seconds` is an optional cap on how long the prompt waits.
When exceeded the prompt raises `PromptUnavailableError`
and the test fails — it does not auto-respond. Use this as a
"don't hang forever" safety net.

## Design rules

### 1. Imperative, not interrogative

The operator is in the middle of something. A statement-shaped
prompt ("Insert UUT, then click Confirm.") is faster to act on
than a question ("Have you inserted the UUT?"). Use the imperative
when you can — questions for `choice` and `input`, statements for
`confirm`.

### 2. Name the concrete action

> ❌ `message="Begin verification protocol"`
>
> ✅ `message="Insert UUT serial 0001 into bench socket 3, then click Confirm."`

The first reads like a spec heading. The second tells the operator
exactly what to do. Include the concrete thing (serial number,
slot, value) when the test knows it.

### 3. One ask per prompt

If the prompt has the word "and" in it, split it:

> ❌ `message="Connect probes 1 and 2, set chamber to 25 °C, then click Confirm."`
>
> ✅ Three prompts — `connect_probes`, `set_chamber`, `confirm_ready` — each individually verifiable.

Multi-step asks lose accountability. Single asks let the operator
back out cleanly at any point and let the test record which step
the operator confirmed.

### 4. Make `choice` lists short and stable

A long `choices` list is a sign you should be picking
programmatically. Limit to options the operator can scan in
two seconds. If you're tempted to pass `choices=["bench_01",
"bench_02", ..., "bench_47"]`, that should be a station-config
field, not a prompt.

### 5. Type-tag `input` in the message

`input` returns a plain string — the prompt itself is the only
place to tell the operator what shape you expect:

> ✅ `message="Enter chamber temperature (°C, integer):"`
>
> ✅ `message="Enter operator initials (3 letters):"`

The test should then validate the response with the right Python
casts and a useful error message on mismatch.

### 6. Set timeout for the maximum sane wait

`timeout_seconds` fails the run with `PromptUnavailableError` when
exceeded — it's a "don't hang the line forever" guard, not a
silent-default. Set it long enough that an operator returning from
a coffee break can still answer (30-300 s is typical for
operator-facing prompts); leave it unset when the test truly needs
to wait indefinitely.

### 7. Match the marker level to the prompt's scope

- File-level (`pytestmark = pytest.mark.litmus_prompts(...)` at module top) — for prompts every test in the module needs.
- Class-scoped — for a group of tests that share a setup prompt.
- Per-test — for prompts that only one test needs.

Putting an operator-specific prompt at file level forces every
test in the file to inherit the marker. More specific markers
override less specific ones on key conflict, so the deepest valid
scope wins.

## Operator UX in the running UI

When the operator UI is up, each prompt becomes a modal at the live monitor. That gives you:

- An amber row in the **ACTIVE TESTS** sidebar block (visible from
  every page) showing "N dialog(s) waiting".
- A modal at the live monitor (`/live/<run_id>`) with the message,
  the choices / input field, and an Acknowledge / Submit button.
- Per-run dialog state preserved across page reloads.

Test from the bench: run `litmus serve`, run a pytest that uses
`prompt`, then walk through what an operator would see. If
something reads ambiguously in a modal, fix the wording before
the production rollout.

## Tips

- **For headless CI, set `LITMUS_AUTO_CONFIRM=1`.** This lets
  prompts auto-resolve so your CI doesn't hang. Make sure the
  defaults in your prompt definitions still produce a useful pass
  (e.g., `choice` auto-picks the first option — order matters).
- **Don't use prompts as sleeps.** A 5-second `timeout_seconds=5`
  on a `confirm` to "wait for the supply to settle" is wrong;
  use `time.sleep()` or an actual condition-poll.
- **Reuse keys across tests.** When two tests need the same
  prompt, define it once at the higher marker scope (file or
  class) instead of repeating the dict.

## See also

- [Reference → litmus_prompts marker](../../reference/pytest/markers.md#litmus_prompts)
- [Concepts → Step hierarchy](../../concepts/execution/step-hierarchy.md) — where prompts sit in the run timeline
- [Tour of the Operator UI](../overview/operator-ui-tour.md) — the ACTIVE TESTS sidebar block, which is your prompt-waiting signal
- [Multi-UUT testing](multi-uut-testing.md) — prompts in subprocess-per-slot setups

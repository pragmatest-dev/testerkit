# Stage 4 — Sidecar config

Test config moved out of Python into a sibling YAML file. Same
vocabulary as pytest decorators: `litmus_vectors`, `litmus_limits`.
The sidecar mirrors pytest's node-id structure: a file-level
`config:` list plus a recursive `tests:` tree where classes are
branches and functions are leaves.

## Diff from stage 3

- Deleted every `@pytest.mark.*` decorator from `test_rail.py`
- Deleted `import pytest` (not needed anymore)
- Added `tests/test_rail.yaml` as a sibling sidecar
- Grouped two idle-state tests under a `TestIdle` class

## Run it

```bash
cd examples/04-sidecar-markers
uv run pytest -v
```

## Why sidecars

Limits and vectors are **configuration**. Keeping them in YAML
separates *what a test does* (Python) from *what values it runs with
or checks against* (YAML). Ops teams can tune limits without
touching code. Reviewers can read a one-file diff for a tolerance
change without scanning test logic.

## Sidecar structure

A file-level `config:` list plus a recursive `tests:` tree. Each
entry under `tests:` is either a function (leaf — just `config:`)
or a class (branch — its own `config:` plus a nested `tests:` for
its methods). The shape mirrors pytest's `file::Class::method`
node ids.

```yaml
config:                           # file-wide: applies to every test
  - litmus_limits: ...

tests:
  test_rail_holds_across_input:   # module-level test (leaf)
    config:
      - litmus_vectors:
          - {vin: [...]}

  TestIdle:                       # class branch
    config:                       # class-wide: applies to every TestIdle method
      - litmus_limits: ...
    tests:
      test_idle_current:          # nested method (leaf)
        config:
          - litmus_limits: ...    # tightens just this method
```

## Classes as sequences

`TestIdle` is a regular pytest class. The methods share setup and a
class-scoped `litmus_limits` entry. Think of a class as "this group
of checks always runs together." Pytest fixture scoping, xunit-style
setup/teardown, and `litmus_vectors` sweeps all work as they normally do.

## The gap this stage leaves

Instrument fixtures are still hand-written in `conftest.py` — every
chapter so far branches on `mock_instruments` to choose `Mock(cls,
...)` vs `cls(resource=...)`. Adding a third instrument means
copying that branch a third time. Stage 5 replaces `conftest.py`
with a **station YAML** — declare your bench once, instrument
fixtures materialize automatically with the same `--mock-instruments`
flag flipping the whole rig into mocked mode for bringup.

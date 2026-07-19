# Stage 4 — Sidecar config

Test config moved out of Python into a sibling YAML file. Same
vocabulary as pytest decorators — minus the `testerkit_` prefix. The
sidecar mirrors pytest's node-id structure: file-level marker fields
(`limits`, `sweeps`, …) plus a recursive `tests:` tree where classes
are branches and functions are leaves.

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

File-level marker fields plus a recursive `tests:` tree. Each entry
under `tests:` is a function (leaf — just marker fields) or a class
(branch — marker fields plus its own nested `tests:`). Reserved
keys at every level are `runner:` (opaque per-runner config) and
`tests:` (nested tree); everything else is a TesterKit marker name.

```yaml
limits:                           # file-wide: applies to every test
  v_rail: ...

tests:
  test_rail_holds_across_input:   # module-level test (leaf)
    sweeps:
      - {vin: [...]}

  TestIdle:                       # class branch
    limits:                       # class-wide: applies to every TestIdle method
      i_idle: ...
    tests:
      test_idle_current:          # nested method (leaf)
        limits:
          i_idle: ...              # tightens just this method
```

## Classes as sequences

`TestIdle` is a regular pytest class. The methods share setup and a
class-scoped `limits` entry. Think of a class as "this group of
checks always runs together." Pytest fixture scoping, xunit-style
setup/teardown, and `sweeps` parametric sweeps all work as they
normally do.

## The gap this stage leaves

Instrument fixtures are still hand-written in `conftest.py` — every
chapter so far branches on `mock_instruments` to choose `Mock(cls,
...)` vs `cls(resource=...)`. Adding a third instrument means
copying that branch a third time. Stage 5 replaces `conftest.py`
with a **station YAML** — declare your bench once, instrument
fixtures materialize automatically with the same `--mock-instruments`
flag flipping the whole rig into mocked mode for bringup.

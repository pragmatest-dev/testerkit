# Stage 4 — Sidecar markers

Markers moved out of Python into a sibling YAML file. Same
vocabulary as pytest decorators: `parametrize`, `litmus_limits`.
Three scopes — file-wide, class-wide, per-test — all in the same
list-of-markers shape.

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

Three scopes, all shaped the same way — a `markers:` list whose
entries mirror pytest decorators:

```yaml
markers:                  # file-wide: applies to every test in the module
  - litmus_limits: ...

classes:
  TestIdle:
    markers:              # class-wide: applies to every method in TestIdle
      - litmus_limits: ...

tests:
  test_rail_holds_across_input:
    markers:              # per-test: applies only to this test
      - parametrize: ...
```

## Classes as sequences

`TestIdle` is a regular pytest class. The methods share setup and a
class-scoped `litmus_limits` entry. Think of a class as "this group
of checks always runs together." Pytest fixture scoping, xunit-style
setup/teardown, and parametrize all work as they normally do.

## The gap this stage leaves

Instruments are still a `FakeDut` class in `conftest.py`. Adding a
real second instrument means hand-writing a second fixture. Stage 5
replaces `conftest.py` with a **station YAML** — declare your
bench once, instrument fixtures materialize automatically, and
`--mock-instruments` flips the whole thing into mocked mode for
bringup.

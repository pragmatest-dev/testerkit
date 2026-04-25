# Stage 4 — Sidecar markers

Markers moved out of Python into a sibling YAML file. Same
vocabulary as pytest decorators: `parametrize`, `litmus_limits`.
The sidecar mirrors pytest's node-id structure: a file-level
`markers:` list plus a recursive `tests:` tree where classes are
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

A file-level `markers:` list plus a recursive `tests:` tree. Each
entry under `tests:` is either a function (leaf — just `markers:`)
or a class (branch — its own `markers:` plus a nested `tests:` for
its methods). The shape mirrors pytest's `file::Class::method`
node ids.

```yaml
markers:                          # file-wide: applies to every test
  - litmus_limits: ...

tests:
  test_rail_holds_across_input:   # module-level test (leaf)
    markers:
      - parametrize: ...

  TestIdle:                       # class branch
    markers:                      # class-wide: applies to every TestIdle method
      - litmus_limits: ...
    tests:
      test_idle_current:          # nested method (leaf)
        markers:
          - litmus_limits: ...    # tightens just this method
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

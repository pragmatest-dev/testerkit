# Profiles — Named Config Sets

A **profile** is a named set of pytest overrides declared in `litmus.yaml` and
selected with `--litmus-profile=<name>`. Use profiles when you want to run the
**same test tree** under different conditions — a quick validation sweep
pre-merge, a full production sweep with retries, a debug profile that enables
`-x -vv` and filters to a single class.

Profiles are purely additive. When no profile is selected, Litmus behaves
exactly as if profiles did not exist.

## Why profiles?

Hardware test suites routinely need to run under multiple lab conditions
without changing code:

- Validation: one voltage, one temperature, fail-fast, skip slow tests
- Production: full vectors, temperature corners, retries on flaky cases
- Debug: single test, verbose, verbose, verbose

Neither CLI flags nor the per-module `test_<name>.yaml` sidecar fit this well:

- CLI flags are ephemeral and can't declare per-test overrides.
- Sidecars are code-adjacent: one set of vectors/limits per test module.

Profiles sit between those two — versioned YAML in the project's `litmus.yaml`
that *can* override specific node-ids while staying opt-in.

## Selecting a profile

```bash
pytest --litmus-profile=validation                       # one profile
pytest --litmus-profile=production --mock-instruments    # profile + other flags
pytest                                                   # no profile → baseline
LITMUS_PROFILE=validation pytest                         # env alias
```

An unknown profile name raises a clean `pytest.UsageError` listing the known
profile names.

## What a profile can do

A profile can override four concerns, each keyed by **pytest node-id**
(`path::Class::method`, `path::func`, or an `fnmatch` glob):

### 1. Vectors — replace parametrized inputs for a test

```yaml
profiles:
  validation:
    vectors:
      "tests/test_power.py::TestRails::test_rails":
        vin: [5.0]               # one voltage instead of three
        temperature: [25]        # skip hot sweep
```

The profile's vectors **replace** the sidecar's for any node-id it matches.
Non-matched tests keep their sidecar (or marker, or native parametrize) vectors
unchanged.

### 2. Limits — override guardbands for a test

```yaml
profiles:
  production:
    limits:
      "tests/test_power.py::TestRails::test_output_voltage":
        output_voltage: {ref: output_voltage, guardband_pct: 5}
```

Profile limits are the final layer in the merge chain
(`sidecar → class marker → method marker → profile`), so profile values win on
overlap and pass through on non-overlap.

### 3. Markers — inject pytest markers onto a test

```yaml
profiles:
  smoke:
    markers:
      "tests/test_power.py::TestSlow":
        - skip: "not run in validation"

  production:
    markers:
      "tests/test_power.py::TestFlaky":
        - flaky: {reruns: 2, reruns_delay: 1}
```

Three YAML shapes are supported per marker spec:

| Shape                             | Equivalent marker                          |
|-----------------------------------|--------------------------------------------|
| `flaky`                           | `@pytest.mark.flaky`                       |
| `skip: "reason"`                  | `@pytest.mark.skip("reason")`              |
| `flaky: {reruns: 2, reruns_delay: 1}` | `@pytest.mark.flaky(reruns=2, reruns_delay=1)` |

Profile markers *accumulate* across overlapping patterns — a rule on
`TestRails::*` adds to any rule on a specific method underneath it. Ecosystem
markers like `flaky` (pytest-rerunfailures) and `dependency`
(pytest-dependency) work out of the box because the profile simply calls
`item.add_marker(...)` — no Litmus-specific parsing.

### 4. Pytest knobs — session-level filters and flags

```yaml
profiles:
  debug:
    pytest:
      addopts: "-x -vv -s"
      keyword: "test_output_voltage"
      markexpr: "not slow"
```

- `addopts` is appended to `PYTEST_ADDOPTS` **before collection**, so every
  downstream plugin (`pytest-rerunfailures`, `pytest-xdist`, `pytest-timeout`)
  sees the args during its own configure phase. Anything you would write on
  the pytest command line fits here.
- `keyword` and `markexpr` act like `-k` and `-m`. If the command line also
  passes `-k` / `-m`, the expressions **AND-compose**: CLI-provided narrowing
  is respected on top of the profile's narrowing.

## Merge order (least → most specific)

```
product spec defaults
    ↓
sidecar test_<module>.yaml
    ↓
class marker (litmus_limits, parametrize)
    ↓
method marker
    ↓
active profile from litmus.yaml
    ↓
CLI flags (--mock-instruments etc.)
```

Profiles sit between markers and CLI: "I authored this test; the profile
tailors it for the lab I'm running in." CLI always wins — explicit intent at
the command line beats recorded profile config.

## Node-id matching

Profile keys match pytest node-ids using `fnmatch`. Exact matches always win
over glob matches (so a class-wide `TestRails::*` rule can coexist with a
per-method pin for one specific test). For markers, exact and glob matches
both accumulate.

```yaml
markers:
  "tests/test_power.py::TestRails::*":   # class-wide skip
    - skip: "skipped in this profile"
  "tests/test_power.py::TestRails::test_one":   # additional method-level marker
    - flaky: {reruns: 3}
```

## Worked example

```yaml
# litmus.yaml
name: power_board_project
default_station: bench_1

profiles:
  validation:
    description: "Quick sweep for pre-merge validation"
    pytest:
      addopts: "-x -vv"
      markexpr: "not slow and not hardware"
    vectors:
      "tests/test_power.py::TestRails::test_rails":
        vin: [5.0]
        temperature: [25]
    markers:
      "tests/test_power.py::TestSlow":
        - skip: "not run in validation"

  production:
    description: "Full sweep, production-grade retries"
    pytest:
      addopts: "--reruns=2 --reruns-delay=1 -n=4"
    vectors:
      "tests/test_power.py::TestRails::test_rails":
        vin: [4.5, 5.0, 5.5]
        temperature: [25, 85]
        load: [0.1, 0.4, 0.8]
    markers:
      "tests/test_power.py::TestRails":
        - flaky: {reruns: 2, reruns_delay: 2}

  debug:
    description: "Single test, verbose, fail-fast"
    pytest:
      addopts: "-x -vv -s"
      keyword: "test_output_voltage"
```

## Provenance

The active profile name is recorded on the run as `profile=<name>` and shows
up in `litmus show <run_id>`. Combined with the git commit and the per-vector
measurement values already recorded on every run, that's enough for an
operator to answer "what config was this test run under?" — no extra drift log
required.

## Non-goals (today)

- Per-directory profile stacking — use one flat namespace in `litmus.yaml`.
- Runtime profile switching mid-session — a profile is session-scoped.
- Profile inheritance (`extends: base_profile`) — may be added if demanded.
- Marker *removal* per profile (negative overrides) — not supported.

## See also

- `docs/reference/configuration.md` — full `profiles:` schema
- `docs/guides/writing-tests.md` — sidecar and marker mechanics
- Pytest plugins commonly combined with profiles:
  - [pytest-rerunfailures](https://github.com/pytest-dev/pytest-rerunfailures)
  - [pytest-xdist](https://github.com/pytest-dev/pytest-xdist)
  - [pytest-timeout](https://github.com/pytest-dev/pytest-timeout)

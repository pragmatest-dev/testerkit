# Profiles

A **profile** is a named set of pytest overrides declared under `profiles:` in
`litmus.yaml` and selected with `--litmus-profile=<name>`. Use profiles when
the **same test tree** must run under different conditions (validation,
production, debug) without code changes.

## Shape

```yaml
# litmus.yaml
profiles:
  <profile_name>:
    description: string       # optional

    pytest:                   # session-level pytest knobs
      addopts: string         # appended to PYTEST_ADDOPTS pre-collection
      markexpr: string        # like -m
      keyword:  string        # like -k

    vectors:                  # keyed by pytest node-id
      "path/to/test.py::TestClass::test_method":
        vin: [5.0]            # REPLACES sidecar vectors for this node-id

    limits:                   # keyed by pytest node-id
      "path/to/test.py::TestClass::test_method":
        output_voltage: {low: 3.25, high: 3.35, units: V}

    markers:                  # injected pytest markers
      "path/to/test.py::TestSlow":
        - skip: "not run in validation"
      "path/to/test.py::TestFlaky":
        - flaky: {reruns: 2, reruns_delay: 1}
```

## Node-id matching

Keys are pytest node-ids (`path::Class::method`, `path::func`) and support
`fnmatch` globs (`TestRails::*`). For vectors and limits, **exact matches win
over globs**. For markers, every match accumulates.

## Marker spec shapes

| YAML                                | Resulting marker                              |
|-------------------------------------|-----------------------------------------------|
| `- flaky`                           | `@pytest.mark.flaky`                          |
| `- skip: "reason"`                  | `@pytest.mark.skip("reason")`                 |
| `- flaky: {reruns: 2, delay: 1}`    | `@pytest.mark.flaky(reruns=2, delay=1)`       |

Ecosystem markers work as-is — profiles just call `item.add_marker(...)`.

## Merge order (least → most specific)

```
sidecar test_<module>.yaml
    ↓
class marker  (litmus_vectors / _limits / _spec / _mocks)
    ↓
method marker
    ↓
active profile                       ← added by --litmus-profile
    ↓
CLI flags
```

Profiles win over sidecars and markers; CLI always wins.

## Selection

```bash
pytest --litmus-profile=validation
LITMUS_PROFILE=validation pytest   # env alias
pytest                              # no profile → baseline behavior
```

Unknown name → `pytest.UsageError` listing known profile names.

## When to propose a profile

Create a profile when the user describes a **recurring lab condition** (e.g.
"same tests but for pre-merge validation", "production rig with retries",
"debug run focused on one test"). Prefer a profile over:

- Modifying the sidecar (that affects all runs, not just this lab).
- Modifying test code (same reason — wrong layer).
- CLI aliases / shell scripts (profiles are versioned, discoverable, and
  typed via `ProjectConfig`).

Do **not** create a profile to change a single knob a user will set once —
that's a CLI flag or an env var.

## What not to put in a profile

- Secrets, credentials, or transport endpoints — those go in station/outputs
  config, not profiles.
- Test body logic (profiles override config, not behavior).
- Per-DUT data — the DUT identity is runtime (`--dut-serial`).

## Provenance

The active profile name is recorded on the run as `profile=<name>` and shows
up in `litmus show <run_id>`. No additional drift-logging is needed — the git
commit plus per-vector measurement values already pin down what config was
applied.

## Cross-references

- `litmus/models/project.py` — `ProfileConfig`, `ProfilePytest`, `ProjectConfig`
- `litmus/execution/plugin.py` — `--litmus-profile` flag, active-profile
  ContextVar, `pytest_load_initial_conftests` addopts injection,
  `pytest_generate_tests` vector layering, `_litmus_push_limits` limit
  layering, `pytest_collection_modifyitems` marker injection
- `docs/guides/profiles.md` — user-facing guide
- `docs/reference/configuration.md` — `profiles:` YAML schema

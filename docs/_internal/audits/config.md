# Config Module Audit — Phase 2 Module 4

**Scope:** `litmus/config/` — 10 files, 2.9k lines.
**Verdict:** clean. The Tier 2 model extraction resolved the prior
"split between schemas.py and config/models.py" debt. What remains is
a lean config package that handles enum definitions, capability models,
test-configuration, YAML formatting, type normalization, and enum
metadata for AI tools.

## Layout after Tier 2

| File | LOC | Role |
|---|---|---|
| `enums.py` | 405 | Shared StrEnum vocabulary (MeasurementFunction, Direction, etc.) |
| `capability.py` | 565 | Signal/Capability/SpecBand hierarchy |
| `test_config.py` | 687 | Limit, RetryConfig, FixtureConfig, TestSequenceConfig |
| `enum_meta.py` | 800 | Abbreviation lookup + metadata registry for AI tooling |
| `loader.py` | 125 | Loads test-config YAML (vectors/limits per test function) |
| `fmt.py` | 119 | `dump_yaml()` / `format_file()` — formatting utility |
| `normalize.py` | 83 | Station instrument-type alias normalization |
| `__init__.py` | 65 | Re-exports from submodules |
| `project.py` | 27 | `load_project_config()` convenience wrapper |

## Findings

### models/config.py shim

`litmus/models/config.py` is a re-export shim that pulls from
`config.capability`, `config.enums`, and `config.test_config`. 57 callers
use it; 21 callers go to the submodules directly. This is the intended
consolidation point. No action needed.

### config/__init__.py

Re-exports the same subset that `models/config.py` does, plus loaders
and enum_meta. `__all__` is explicit and well-curated. No action needed.

### config/project.py

27-line convenience function `load_project_config()` that defaults to
`litmus.yaml` in cwd and falls back to a default `ProjectConfig`. Used
by 5 callers (`plugin.py`, `api/app.py`, `output_runner.py`). Cleanly
delegates to `store.load_project`. No action needed.

### enum_meta.py (800 lines)

Mostly a data dict mapping each `MeasurementFunction` and `ConditionKey`
value to metadata (abbreviations, display names, measurement categories).
Plus `lookup_enum()` reverse-index for AI shorthand resolution. The size
is unavoidable (the domain has 50+ measurement functions). No action
needed.

### config/loader.py vs store.py

No overlap. `loader.py` loads per-test-function config (`config.yaml`
beside the test file). `store.py` loads entity YAML (stations, fixtures,
etc.). Distinct responsibilities. No action needed.

## Changes applied

None. The module is clean.

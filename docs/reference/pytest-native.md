# pytest-native reference

Litmus is a hardware test **platform**; pytest is its primary test-runner integration. The bundled pytest plugin slots into a stock pytest install with zero configuration — every pytest concept (collection, fixtures, markers, plugins, `conftest.py`, command-line flags) works unchanged. This page is the map of what pytest gives you natively and what the plugin layers on top. For the plugin's own surface see [Litmus fixtures](litmus-fixtures.md) and [Litmus markers](litmus-markers.md). Other runner integrations (OpenHTF, LabVIEW / TestStand via the results API) live under [Integrations](../integration/index.md).

## Collection

The bundled pytest plugin uses pytest's default collection. No custom collectors, no replacement of `pytest_collect_file`.

| Convention | Default |
|---|---|
| Test files | `tests/test_*.py` or `tests/*_test.py` |
| Test classes | classes named `Test*` (no `__init__`) |
| Test functions / methods | functions named `test_*` |
| Override | standard pytest `python_files` / `python_classes` / `python_functions` in `pyproject.toml` |

What Litmus adds at collection time:

- A `pytest_collection_modifyitems` hook merges per-test sidecar YAML (`tests/test_<module>.yaml`) into each item's marker set. This expands `litmus_sweeps` into one pytest case per row exactly as if you had written `@pytest.mark.parametrize` — pytest still owns the case multiplication.
- Profiles (`--test-profile=<name>`) add `pytest.mark.skip` to items they exclude. The selection is visible in `pytest --collect-only -q`.

The sidecar is recursive: top-level keys apply to every test in the file; `tests: { ClassName: { ... } }` scopes per class; `tests: { ClassName: { tests: { test_method: { ... } } } }` scopes per method. See [Test configuration](configuration.md#test-configuration).

## Fixtures

pytest's fixture model is unchanged.

- **All four scopes work.** `function`, `class`, `module`, `session` — choose the one that matches your resource cost.
- **Resolution by name.** Take a fixture in the test signature; pytest resolves it from the nearest `conftest.py` upward, then from registered plugins (Litmus included).
- **Yield fixtures, finalizers, request injection** all work as pytest documents them.
- **`autouse=True`** works. Litmus's own `logger` fixture is autouse-session so every test sees an active logger without taking it as an argument.

You can write your own fixtures in `conftest.py` alongside Litmus's. A common pattern is a project-local `dut` factory wrapping the Litmus `dut` session fixture, or a per-class hardware-setup fixture that takes `instruments` as a dependency.

The 20 fixtures the Litmus plugin contributes are documented in [Litmus fixtures](litmus-fixtures.md).

## Markers

pytest's marker mechanism is unchanged. All of these work on Litmus tests as documented in the pytest manual:

| Marker | Use |
|---|---|
| `@pytest.mark.parametrize` | Generate one pytest case per row. Stack to cross-product. |
| `@pytest.mark.skip` / `@pytest.mark.skipif` | Skip the test (with optional condition). |
| `@pytest.mark.xfail` | Mark as expected-to-fail; surfaces as `XFAIL` / `XPASS`. |
| `@pytest.mark.usefixtures("a", "b")` | Require fixtures without taking them in the signature. |
| `@pytest.mark.filterwarnings` | Per-test warning filters. |
| Custom markers via `pytest.ini` / `pyproject.toml` | Register and filter with `-m`. |

The seven `@pytest.mark.litmus_*` markers Litmus adds live in the same registry and stack with native ones (with one constraint — see the [no-stacking rule](litmus-markers.md#no-stacking-rule) for Litmus markers). See [Litmus markers](litmus-markers.md) for full details.

`@pytest.mark.parametrize` and `@pytest.mark.litmus_sweeps` interoperate: both feed the same `context.get_param(name)` API and the same parquet `in_*` columns at runtime.

## conftest.py

Works as pytest documents it. Place a `conftest.py` in `tests/` (or a subdirectory) for fixtures and hooks that apply to that scope. Common uses with Litmus:

- Project-local fixtures that wrap or extend Litmus's (a typed `dut` accessor, a per-class measurement helper).
- `pytest_addoption` for project-specific CLI flags.
- `pytest_collection_modifyitems` for project-specific item filtering. (Litmus's hook is in the plugin and runs alongside, not instead of, yours.)
- `pytest_runtest_setup` / `_teardown` for per-test setup beyond what fixtures express.

The Litmus plugin loads via the standard pytest entry-point mechanism — no `conftest.py` manipulation needed.

## Command-line flags

All pytest flags work. The ones that matter most for hardware test work:

| Flag | Purpose |
|---|---|
| `-k "expr"` | Run tests whose nodeid matches the substring expression. |
| `-m "marker"` | Run tests whose markers match the boolean expression. |
| `-x` / `--maxfail=N` | Stop after the first failure / Nth failure. |
| `--lf` / `--ff` | Run last-failed / failed-first (uses pytest's cache). |
| `--collect-only -q` | Show what would run without running. |
| `-v` / `-q` | Verbose / quiet. |
| `--tb=short` / `--tb=line` / `--tb=no` | Traceback style. |
| `-p no:plugin` | Disable a specific plugin. |
| `--co` | Alias for `--collect-only`. |

Litmus adds the following flags (see [CLI reference](cli.md) for the full set):

| Flag | Purpose |
|---|---|
| `--station <id-or-path>` | Resolve a `stations/*.yaml` to activate. |
| `--product <id-or-path>` | Resolve a `products/*.yaml` to drive spec lookup. |
| `--dut-part-number <pn>` | Content match against `product.part_number:`. |
| `--fixture <id-or-path>` | Resolve a `fixtures/*.yaml` for pin → instrument routing. |
| `--test-profile <name>` | Apply a named profile (test selection + overrides). Pair with `--no-test-profile` to disable a `default_profile:` set in `litmus.yaml`. |
| `--mock-instruments` | Replace every real instrument with a mock. |
| `--guardband <pct>` | Tighten spec-derived limits for manufacturing margin. |
| `--data-dir <path>` | Override the canonical results directory. |

## Plugins that interact with Litmus

| Plugin | Notes |
|---|---|
| **pytest-rerunfailures** | Powers `@pytest.mark.litmus_retry`. Install it if you use the marker; the translation happens in the plugin. |
| **pytest-xdist** | Parallel execution. Generally **not** appropriate for hardware tests on a single bench (instruments aren't reentrant). Fine for mock-only suites and CI lint passes. |
| **pytest-cov** | Code coverage. Works unchanged on test files; collects coverage on the test code, not on instrument drivers behind hardware. |
| **pytest-html** / **pytest-json-report** | Independent of Litmus's own event log + parquet output. Run them alongside if you want pytest-flavored reports too. |

## Discovery vs activation

Two things to keep separate:

- **Discovery** is pytest's: what `tests/test_*.py` files match, what items they expose. Litmus has no opinion here.
- **Activation** is Litmus's: which station, product, fixture, profile is loaded for the session. Driven by the CLI flags above (or by `default_station:` / `default_profile:` in `litmus.yaml`).

A test that runs on a bringup tier with no station YAML and a test that runs on a factory tier with full traceability are **collected identically**. The activation context decides what fixtures resolve to and what limits `verify` finds.

## See also

- [Litmus fixtures](litmus-fixtures.md) — all 20 fixtures the plugin contributes
- [Litmus markers](litmus-markers.md) — the seven `litmus_*` markers and their sidecar equivalents
- [Test configuration](configuration.md#test-configuration) — sidecar YAML merge semantics
- [CLI reference](cli.md) — full flag list, including the non-pytest commands (`litmus serve`, `litmus runs`, etc.)
- [pytest documentation](https://docs.pytest.org/en/stable/) — canonical reference for everything in the "pytest-native" half of this page

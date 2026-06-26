# Tests

**URLs:** `/tests` (list), `/tests/{path}` (detail)

A test in Litmus is a `def test_*` function that pytest discovers — either a
top-level function or a method inside a `class Test*`. The Tests pages give you
a file-level inventory of every `test_*.py` under `tests/`, cross-referenced
against run history, without importing or executing any test code.

## List — `/tests`

Files are grouped by directory. Each directory renders a folder-icon header
followed by one row per `test_*.py` file under it.

### File rows

Each row shows:

| Field | What it shows |
|---|---|
| Filename | The `test_*.py` filename, in monospace |
| Test count | Number of `def test_*` functions found in the file (top-level and inside `class Test*`) |
| Class count | Number of `class Test*` definitions, shown inline when non-zero |
| Vectors | A `~N vectors` badge, present when the file has parametrize, `litmus_sweeps`, or similar list-valued markers. The count is a rough upper-bound estimate from the decorator arguments — not an exact cross-product |
| Markers | Up to four marker chips (decorator names, without the `pytest.mark.` prefix), then `+N` when there are more |
| Runs | Count of distinct `run_id` values in history where any step from this file executed; omitted when zero |
| Failed | Count of those runs with at least one failed step; omitted when there are no runs or no failures |
| Sidecar | A `sidecar` badge when a `.yaml` file with the same stem exists alongside the `.py`; a dash otherwise |

Clicking any row navigates to `/tests/{path}` for that file.

When a file cannot be parsed (syntax error or I/O problem), the row renders in
red with a warning icon and a truncated error message. The file is still
clickable — the detail page shows the full parse error.

### Badge

A `N files` badge in the page header shows the total number of `test_*.py`
files found.

### Observed in history (no matching source)

Below the file list, when run history contains step paths that do not match
any function in the current source, an **Observed in history (no matching
source)** section appears. This surfaces tests that were renamed, deleted, or
run from a code state that isn't checked out. The section shows the count of
orphaned paths as a warning badge.

The orphan table has these columns:

| Column | What it shows |
|---|---|
| Step Path | The `step_path` value from the steps table — bare function name or `Class/method` |
| Runs | Distinct run count |
| Passed | Runs with outcome `passed` |
| Failed | Runs with outcome `failed` |
| Last Run | Most recent step start timestamp, browser-local time |

Rows are sorted by Last Run descending so the most recently orphaned paths appear first.

### Empty state

When no `test_*.py` files are found and there are no orphan step paths, the
file list is replaced with a card:

> No test files found.
>
> Add test_*.py files under tests/.

### Data-testid anchors

| Element | `data-testid` |
|---|---|
| Directory-grouped file list container | `tests-table` |
| Orphaned step paths table | `tests-orphans-table` |

## Detail — `/tests/{path}`

The `{path}` segment carries the full relative path from the project root,
including the `tests/` prefix and the `.py` extension — for example,
`/tests/tests/test_power_rail.py`. The path is resolved against the server's
current working directory; only files inside the working tree are served.

### Header

The page header shows the file path, a `N tests` badge, a `sidecar` badge
when a sidecar exists, and a **Launch Test** button (top right) that navigates
to `/launch?test={path}`.

A note below the header reads:

> What actually runs depends on the active profile — sidecar < profile
> (last-wins). See Profiles.

"Profiles" is a link to `/profiles`. For the cascade order, see
[Profiles](../configuration.md#profile-blocks-under-profiles).

### Test functions table

A table with one row per `def test_*` function found in the file. Columns:

| Column | What it shows |
|---|---|
| Test | Bare function name (`test_foo`) |
| Class | Parent `class Test*` name, or `—` for top-level functions |
| Markers | Comma-separated decorator names (without `pytest.mark.` prefix), or `—` |
| Vectors | Estimated parametrize / `litmus_sweeps` vector count, or `—` when zero |
| Sidecar | `✓` when this function (or its parent class) has an entry in the sidecar `tests:` block; `—` otherwise |
| Runs | Distinct run count from history for this step path; `0` when never run |
| Passed | Runs with outcome `passed` |
| Failed | Runs with outcome `failed` |
| Last Run | Most recent step start timestamp, browser-local time; `—` when never run |

The step path used to look up run history is `Class/method` when the function
is inside a class, or the bare function name for top-level functions. This
matches the `step_path` value recorded in run history.

The test functions table is omitted when the file has a parse error.

Data-testid anchor: `test-functions-table`.

### Tabs

Below the test functions table, two tabs are available:

| Tab | Content |
|---|---|
| Code | The `.py` file rendered as a read-only Python code block |
| Sidecar YAML | The colocated `.yaml` file rendered as a read-only YAML code block; present only when a sidecar exists |

When no sidecar exists, only the **Code** tab appears.

### Parse error state

When the file cannot be parsed, a red card appears at the top of the content
area with the full error message. The Code tab still renders the raw file
contents.

### Not-found state

When `{path}` does not resolve to an existing `.py` file inside the working
tree, the page shows a card:

> Test file '{path}' not found.

with a "← Back to Tests" link.

## Underlying data

The file list is built by reading every `test_*.py` under `tests/` directly —
without importing or running any test code — to extract its function names,
class names, and decorators. Files that fail to parse are still listed,
flagged with the parse error.

Run history (Runs / Passed / Failed / Last Run) is tallied from run history
for each test, by step path. For the full step record schema, see
[Parquet schema](../data/parquet-schema.md).

A test shows the `sidecar` badge when a `.yaml` file with the same stem sits
alongside the `.py`. On the detail page, a function gets the `✓` when it (or
its class) has an entry in the sidecar's `tests:` block.

## See also

- [How-to → Writing Tests](../../how-to/execution/writing-tests.md) — pytest
  classes, sidecar YAML, the `verify` pattern
- [pytest markers reference](../pytest/markers.md) — every `@litmus.mark.*`
  decorator that appears in the Markers column
- [Profiles](profiles.md) — how profiles override sidecar config at session
  start (the cascade note on the detail page)
- [Launch Test](launch.md) — start a run targeting one of these test files
- [Results](results/list.md) — per-run view; the step-level detail that feeds
  the Runs / Passed / Failed counts on this page

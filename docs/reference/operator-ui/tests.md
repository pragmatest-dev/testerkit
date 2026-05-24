# Tests

**URL:** `/tests`

A flat table of the test directories Litmus discovered in the
project — one row per directory containing `test_*.py` files. This
is the lightweight inventory view; for actually running a test, use
[Launch Test](launch.md).

## Table

| Column | What it shows |
|---|---|
| Name | Directory name (e.g. `smoke`, `production`, `regression`) |
| Path | Filesystem path relative to the project root |

Both columns sort. There are no per-row actions — rows are not
clickable. The Launch Test form's **Test Path** dropdown is
populated from the same `discover_tests()` call, so any directory
that appears here is selectable when starting a run.

## Empty state

When no test directories are found, the table is replaced with a
card reading "No test directories found." with a hint: "Add
test\_\*.py files to a tests/ directory."

## Underlying data

Tests come from `tests/` under the directory `litmus serve` was
started from (the resolved current working directory, not the
project root from `litmus.yaml`). Discovery walks `tests/`
recursively at any depth and reports every directory that contains
at least one `test_*.py` file — both the bare `tests/` directory
and nested subdirectories at any depth.

The displayed Name column is the immediate parent directory of the
discovered test file, so `tests/smoke/regression/test_foo.py` lists
as Name `regression`, Path `tests/smoke/regression`.

## Common tasks

- **Run a test from one of these directories** — open
  [Launch Test](launch.md), pick the Test Path from the dropdown.

## See also

- [Launch Test](launch.md) — start a run targeting one of these test directories
- [Tutorial → First Test](../../tutorial/01-first-test.md) — how to write a `test_*.py` file

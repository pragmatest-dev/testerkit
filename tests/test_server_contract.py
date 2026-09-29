"""Contract tests for docs/44 §1's compatibility rule (testerkit-server's
docs/44-single-source-model-and-projection.md: "[RULE] testerkit-server
depends on testerkit and stays compatible with every testerkit change").

Two independent checks:

1. **Symbol contract** — every testerkit symbol testerkit-server imports
   (from an AST scan of its ``testerkit_server/``, ``tests/``, ``scripts/``
   and ``bench/`` trees — see ``scripts/generate_server_compat_list.py``)
   must still exist and be importable. This is the half of docs/44 §1's
   cross-repo gate that works even when the sibling testerkit-server
   checkout is absent (CI, a solo clone, a release branch): it can't run
   the server's own suite, but it can catch "this symbol was renamed /
   deleted" without leaving the repo.

2. **Builder signature pins** — the handful of *builder* functions the
   server calls positionally/by-keyword (the SQL-builders whose signature
   IS their contract, not just their existence). ``_occurrence_index_expr``
   is here because its signature changing (`(*, vector_index_expr)` ->
   `(*, run_id, name, step_index, step_path, vector_index)`) already broke
   testerkit-server once in production (docs/44 audit, `.tmp/
   audit-plan-vs-built.md` "Known break") with no test catching it — this
   pins it so that class of break fails HERE.

A third check, module-list drift, needs the sibling testerkit-server
checkout to regenerate against; it's skipped (not failed) when that
checkout isn't present, since there is nothing to compare against.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_REPO_ROOT / "scripts"))
import generate_server_compat_list as _gen_compat  # noqa: E402  (path insert above)

# Every testerkit symbol testerkit-server imports, keyed by dotted module
# path. Kept in sync BY HAND with scripts/server_compat_modules.txt's
# module list (that file is what's authoritative for *which modules*;
# this dict additionally names *which symbols* within them, since a
# missing symbol — not just a missing module — is the actual break). If a
# module here stops appearing in the generated list, drop it from both;
# if the generated list gains a module, add its imported symbols here.
_SERVER_IMPORTED_SYMBOLS: dict[str, tuple[str, ...]] = {
    "testerkit": ("Mock", "linspace"),
    "testerkit.analysis.measurements_query": (
        "_PARETO_SQL",
        "_PPK_SQL",
        "_RETEST_SQL",
        "_TIME_LOSS_SQL",
        "_TREND_SQL",
        "_YIELD_OVERALL_SQL",
        "_period_col",
    ),
    "testerkit.analysis.runs_query": ("usage_stats_sql",),
    "testerkit.cli.forward_cmd": ("_channel_wire_table",),
    "testerkit.data._accumulator_pool": ("AccumulatorPool",),
    "testerkit.data._catalog_keys": ("EVENTS_KEY", "FILES_KEY", "file_storage_key"),
    "testerkit.data._schema_keys": (
        "IO_KEY",
        "MEASUREMENT_FACTS_KEY",
        "RUNS_KEY",
        "STEPS_KEY",
        "VECTORS_KEY",
        "null_safe_key_columns",
    ),
    "testerkit.data._sql_helpers": (
        "glob_to_like_pattern",
        "has_wildcard",
        "multi_filter_clauses",
        "partition_exact_and_glob",
        "sql_escape",
    ),
    "testerkit.data.backends._event_accumulator": ("EventAccumulator",),
    "testerkit.data.backends.parquet": (
        "_build_unified_rows_from_acc",
        "materialize_run_to_parquet",
    ),
    "testerkit.data.channels.index": ("ChannelIndex",),
    "testerkit.data.channels.models": ("ChannelDescriptor",),
    "testerkit.data.channels.store": ("ChannelStore",),
    "testerkit.data.channels.window": (
        "decimate_table",
        "decode_value_column",
        "dedup_on_sample_offset",
    ),
    "testerkit.data.event_store": ("_parse_event_row",),
    "testerkit.data.events": (
        "MeasurementRecorded",
        "RunEnded",
        "RunStarted",
        "StepEnded",
        "StepStarted",
    ),
    "testerkit.data.measurement_projection": (
        "IO_ROW_COLUMNS",
        "MEASUREMENTS_COLUMNS",
        "STEPS_COLUMNS",
        "VECTORS_COLUMNS",
        "WORST_STEP_OUTCOME_EXPR",
        "_RUN_CONTEXT_COLUMNS",
        "_occurrence_index_expr",
        "inputs_projection_select",
        "io_projection_select",
        "measurements_projection_select",
        "outputs_projection_select",
        "steps_projection_select",
        "vectors_projection_select",
    ),
    "testerkit.data.read_models": (
        "MEASUREMENTS_SLIM_COLUMNS",
        "RunDetail",
        "RunRow",
        "derive_run",
        "read_model_fingerprint",
        "run_detail",
    ),
    "testerkit.data.run_projection": ("DURATION_S_EXPR", "runs_projection_select"),
    "testerkit.data.schemas": (
        "RUN_ROW_SCHEMA",
        "SCHEMA_VERSION",
        "_build_write_schema",
        "table_from_rows",
    ),
    "testerkit.execution.verify": ("_compute_outcome",),
    "testerkit.models.test_config": ("Limit",),
    "testerkit.queries": ("RunsQuery",),
    "testerkit.replication": (
        "BatchDisposition",
        "EVENT_WAL_SCHEMA",
        "ingest_replicated",
        "read_closed_channel_segments",
    ),
    # docs/41 live overlay: the server stores what the bench pushes, reusing these
    # wire models and projection (never redefined server-side).
    "testerkit.data.live_projection": ("project_run",),
    "testerkit.data.live_rows": (
        "HARD_DOC_BYTES",
        "LIVE_WIRE_VERSION",
        "MAX_BODY_BYTES",
        "LiveDoc",
        "LiveHeader",
        "LivePush",
        "LivePushResponse",
        "LiveSyncState",
        "estimate_doc_size",
        "rejoin",
        "row_id_for",
    ),
    "testerkit.store": ("find_catalog_dirs", "load_catalog_from_directory", "load_project"),
    # The cloud Channels view's session labels are a TypeScript port of these;
    # the server's parity test runs them against a shared fixture.
    "testerkit.ui.shared": ("components",),
}


def _flat_cases() -> list[tuple[str, str]]:
    return [
        (module, symbol)
        for module, symbols in _SERVER_IMPORTED_SYMBOLS.items()
        for symbol in symbols
    ]


@pytest.mark.parametrize(
    "module_name,symbol", _flat_cases(), ids=lambda v: v if isinstance(v, str) else None
)
def test_server_imported_symbol_exists(module_name: str, symbol: str) -> None:
    """Every symbol testerkit-server imports must still be importable.

    This is the part of the docs/44 §1 gate that runs with NO sibling
    testerkit-server checkout present — it can't prove the server's tests
    still pass, but it catches the cheapest, most common break: a rename
    or deletion of something the server reaches into.
    """
    module = importlib.import_module(module_name)
    assert hasattr(module, symbol), (
        f"testerkit-server imports `{symbol}` from `{module_name}`, but it no longer exists.\n"
        "This is a breaking change for testerkit-server (docs/44 §1) — coordinate before landing."
    )


# The builder functions whose SIGNATURE (not just existence) is the
# server's actual contract with them — every one is called with the exact
# argument shape pinned below somewhere in testerkit-server's serving or
# derive path.
# Values are `str(inspect.signature(func))` WITHOUT evaluating string
# annotations (these modules use `from __future__ import annotations`, so
# annotations are the literal source text, quoted). Deliberately not using
# `eval_str=True` here: evaluating turns `Path` into a fully-qualified
# runtime module path (`pathlib._local.Path` on 3.13, `pathlib.Path` on
# 3.12 — an interpreter-internal detail, not part of the contract), which
# would make the pin flaky across Python versions.
_STR_ARG_STR = "(source_sql: 'str') -> 'str'"
_PINNED_SIGNATURES: dict[str, str] = {
    "testerkit.data.measurement_projection:io_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:inputs_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:outputs_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:measurements_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:steps_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:vectors_projection_select": _STR_ARG_STR,
    "testerkit.data.measurement_projection:_occurrence_index_expr": (
        "(*, run_id: 'str', name: 'str', step_index: 'str', step_path: 'str', "
        "vector_index: 'str') -> 'str'"
    ),
    "testerkit.data.run_projection:runs_projection_select": _STR_ARG_STR,
    "testerkit.data.read_models:derive_run": (
        "(source: 'pa.Table | str | Path', *, file_path: 'str | None' = None) -> 'DerivedRun'"
    ),
    "testerkit.data.read_models:run_detail": (
        "(source: 'pa.Table | str | Path', *, file_path: 'str | None' = None) -> 'RunDetail'"
    ),
    "testerkit.data.read_models:read_model_fingerprint": "(name: 'str') -> 'str'",
}


@pytest.mark.parametrize("qualname,expected", sorted(_PINNED_SIGNATURES.items()))
def test_builder_function_signature_is_pinned(qualname: str, expected: str) -> None:
    """A signature change here is a breaking change for testerkit-server
    even though the symbol still exists (the historical break this test
    exists to catch — see module docstring). Changing a builder's
    signature is fine; it must come with an update HERE plus a
    coordinated change on the server side, not a silent drift.
    """
    module_name, func_name = qualname.split(":")
    module = importlib.import_module(module_name)
    func = getattr(module, func_name)
    actual = str(inspect.signature(func))
    assert actual == expected, (
        f"`{qualname}`'s signature changed:\n  was:  {expected}\n  now:  {actual}\n"
        "testerkit-server calls this by this shape (docs/44 §1) — update the pin above "
        "AND coordinate the server-side change, don't just silence this test."
    )


def test_compat_module_list_matches_server_imports() -> None:
    """scripts/server_compat_modules.txt (the pre-commit gate's file-touch
    list) must match what testerkit-server's source actually imports.
    Regenerate with `uv run python scripts/generate_server_compat_list.py`
    if this fails for a legitimate reason (the server started/stopped
    importing something).

    Skipped, not failed, when no sibling testerkit-server checkout is
    available to scan (nothing to diff against).
    """
    server_dir = _gen_compat.default_server_dir()
    if not server_dir.is_dir():
        pytest.skip(f"no sibling testerkit-server checkout at {server_dir} to check drift against")

    committed = _gen_compat._OUTPUT_PATH.read_text() if _gen_compat._OUTPUT_PATH.exists() else ""
    regenerated = _gen_compat.render(_gen_compat.scan_server_repo(server_dir))
    assert committed == regenerated, (
        "scripts/server_compat_modules.txt is stale. Regenerate with:\n"
        "  uv run python scripts/generate_server_compat_list.py"
    )


def test_every_symbol_in_the_contract_is_also_in_the_generated_module_list() -> None:
    """Catches the dict above going stale in the other direction: a module
    removed from ``_SERVER_IMPORTED_SYMBOLS`` (or never added) while it's
    still in the committed module list, silently narrowing the contract
    test's coverage."""
    committed = _gen_compat._OUTPUT_PATH.read_text() if _gen_compat._OUTPUT_PATH.exists() else ""
    committed_modules = {
        line.strip()
        for line in committed.splitlines()
        if line.strip() and not line.strip().startswith("#")
    }
    contract_modules = set(_SERVER_IMPORTED_SYMBOLS)
    assert contract_modules == committed_modules, (
        f"only in generated list: {committed_modules - contract_modules}\n"
        f"only in this test's contract dict: {contract_modules - committed_modules}\n"
        "Keep tests/test_server_contract.py::_SERVER_IMPORTED_SYMBOLS and "
        "scripts/server_compat_modules.txt in sync."
    )

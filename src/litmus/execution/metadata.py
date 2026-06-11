"""Runner-neutral run-metadata assembly.

:class:`TestRunLogger` takes a fat kwargs dict — DUT serial, station
identity, part identity, fixture id, environment capture, project
name, profile name + facets, session inputs, instrument records, etc.
The dict is the same regardless of which runner is driving; only the
*sources* differ (pytest reads CLI options + session fixtures, OpenHTF
reads its config object, etc.).

This module owns the assembly: :func:`build_run_metadata` takes
already-resolved inputs and returns the kwargs dict ready to hand to
:class:`TestRunLogger`. Each runner's plugin gathers the inputs in its
own way and calls in.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from litmus.environment import capture_environment
from litmus.execution._git import get_project_name


def build_run_metadata(
    *,
    dut_serial: str | None,
    dut_part_number: str | None = None,
    dut_revision: str | None = None,
    dut_lot_number: str | None = None,
    station_id: str | None = None,
    station_config: Any | None = None,
    fixture_config: Any | None = None,
    part_context: Any | None = None,
    operator_id: str | None = None,
    project_dir: Path,
    data_dir: str | None = None,
    test_phase: str | None = None,
    profile_name: str | None = None,
    profile_facets: dict[str, str] | None = None,
    session_inputs: dict[str, str] | None = None,
    instrument_records: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build the kwargs dict :class:`TestRunLogger` expects.

    Resolves derived fields (part info from ``part_context``,
    station fields from ``station_config``, environment capture, git
    project name) so the runner's plugin doesn't have to. ``dut_part_number``
    and ``dut_revision`` fall back to the active part when not
    supplied explicitly.
    """
    # Part info from part_context
    part_id = part_name = part_revision = None
    if part_context is not None:
        part_id = part_context.part.id
        part_name = part_context.part.name
        part_revision = part_context.part.revision

    # Fixture id
    fixture_id = None
    if fixture_config is not None:
        fixture_id = getattr(fixture_config, "id", None) or getattr(fixture_config, "name", None)

    # Station info
    station_name = station_type = station_location = None
    if station_config is not None:
        station_name = station_config.name
        station_type = getattr(station_config, "station_type", None) or getattr(
            station_config, "type", None
        )
        station_location = station_config.location

    # DUT defaults from part spec
    if dut_part_number is None and part_context is not None:
        dut_part_number = part_context.part.part_number
    if dut_revision is None and part_context is not None:
        dut_revision = part_context.part.revision

    return {
        "dut_serial": dut_serial,
        "dut_part_number": dut_part_number,
        "dut_revision": dut_revision,
        "dut_lot_number": dut_lot_number,
        "station_id": station_id,
        "station_name": station_name,
        "station_type": station_type,
        "station_location": station_location,
        "operator_id": operator_id,
        "part_id": part_id,
        "part_name": part_name,
        "part_revision": part_revision,
        "fixture_id": fixture_id,
        "project_name": get_project_name(project_dir),
        "project_dir": project_dir,
        "data_dir": data_dir,
        "test_phase": test_phase,
        "profile": profile_name,
        "profile_facets": dict(profile_facets or {}),
        "session_inputs": dict(session_inputs or {}),
        "instruments": instrument_records,
        "environment": capture_environment(),
    }

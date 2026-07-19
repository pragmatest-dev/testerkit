"""Response models for HTTP endpoints in :mod:`testerkit.api.app`.

Wraps the dict-shaped JSON payloads each endpoint returns into typed
Pydantic models so the auto-generated OpenAPI (``/api/openapi.json``)
carries enough type information for consumers to codegen against.

Naming convention: ``XListResponse`` for endpoints that wrap a list
under a single key (e.g. ``{"runs": [...]}``); ``XResponse`` for
endpoints that wrap a single object.

Most wrappers reference existing models elsewhere in the codebase
(:class:`~testerkit.analysis.runs_query.RunRow`,
:class:`~testerkit.analysis.steps_query.StepRow`, etc.). A few endpoints
delegate to query functions that return ad-hoc dicts — those use a
loose ``data: list[dict[str, Any]]`` shape with a note that the
schema crystallises at 1.0.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from testerkit.analysis.runs_query import RunRow
from testerkit.analysis.steps_query import StepNode, StepRow
from testerkit.api.schemas import CapabilitySummary, RequirementSummary

# ---------------------------------------------------------------------------
# Runs
# ---------------------------------------------------------------------------


class RunsListResponse(BaseModel):
    """``GET /runs`` — list of recent runs (denormalized run-level summaries)."""

    runs: list[RunRow]


class MeasurementsListResponse(BaseModel):
    """``GET /runs/{run_id}/measurements`` — flat measurement rows.

    Each row mirrors the parquet ``record_type='measurement'`` shape
    with all run / step / measurement context denormalized.
    """

    measurements: list[dict[str, Any]]


class StepsListResponse(BaseModel):
    """``GET /runs/{run_id}/steps`` — ordered step rows."""

    steps: list[StepRow]


class StepsTreeResponse(BaseModel):
    """``GET /runs/{run_id}/steps/tree`` — hierarchical step tree."""

    tree: list[StepNode]


class RunLaunchResponse(BaseModel):
    """``POST /runs`` — kick-off acknowledgement."""

    run_id: str
    status: Literal["running"]


# ---------------------------------------------------------------------------
# Active runs
# ---------------------------------------------------------------------------


class ActiveRunsResponse(BaseModel):
    """``GET /active`` — currently-tracked runs."""

    active_runs: list[dict[str, Any]]
    count: int


# ---------------------------------------------------------------------------
# Dialogs
# ---------------------------------------------------------------------------


class DialogsListResponse(BaseModel):
    """``GET /dialogs`` — pending operator dialogs."""

    dialogs: list[dict[str, Any]]


class DialogCreateResponse(BaseModel):
    """``POST /dialogs`` — registration acknowledgement."""

    dialog_id: str
    status: Literal["pending"]


class DialogRespondAck(BaseModel):
    """``POST /dialogs/{dialog_id}/respond`` — response acknowledgement."""

    status: Literal["ok"]


# ---------------------------------------------------------------------------
# Parts & Stations
# ---------------------------------------------------------------------------


class PartsListResponse(BaseModel):
    """``GET /parts`` — part summaries (id + label-only fields)."""

    parts: list[dict[str, Any]]


class PartRequirementsResponse(BaseModel):
    """``GET /parts/{part_id}/requirements`` — required capabilities."""

    part_id: str
    requirements: list[RequirementSummary]


class StationsListResponse(BaseModel):
    """``GET /stations`` — station summaries."""

    stations: list[dict[str, Any]]


class StationCapabilitiesResponse(BaseModel):
    """``GET /stations/{station_id}/capabilities`` — what the station provides."""

    station_id: str
    capabilities: list[CapabilitySummary]


class MatchSingleResponse(BaseModel):
    """``GET /match?part_id=X&station_id=Y`` — one-station match check."""

    part_id: str
    station_id: str
    compatible: bool


class MatchAllResponse(BaseModel):
    """``GET /match?part_id=X`` — all-stations match result."""

    part_id: str
    stations: list[dict[str, Any]]


# ---------------------------------------------------------------------------
# Instruments & Catalog
# ---------------------------------------------------------------------------


class InstrumentTypesResponse(BaseModel):
    """``GET /instruments/types`` — distinct catalog instrument types."""

    instrument_types: list[str]


class InstrumentAssetsResponse(BaseModel):
    """``GET /instruments/assets`` — physical-device asset files."""

    assets: list[dict[str, Any]]
    count: int


# ---------------------------------------------------------------------------
# Manufacturing-test analytics
# ---------------------------------------------------------------------------


class MetricsResponse(BaseModel):
    """Shared shape for ``GET /metrics/*`` endpoints.

    Each metric (``summary``, ``pareto``, ``ppk``, ``trend``,
    ``retest``, ``time-loss``) wraps its DuckDB-aggregated rows under
    ``data``. The row shape is metric-specific; this stays loosely
    typed until 1.0 when the analytics surface freezes.
    """

    data: list[dict[str, Any]] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Loose passthrough — events / sessions / channels / MCP parity
# ---------------------------------------------------------------------------
#
# These endpoints delegate to MCP tool functions or DuckDB query
# helpers that return ad-hoc dict payloads. Until the MCP tool surface
# crystallises with typed contracts (post-0.1.0), use a permissive
# ``dict[str, Any]`` shape and document the actual keys in the
# endpoint docstring.


class GenericObjectResponse(BaseModel):
    """Permissive passthrough for endpoints that return ad-hoc objects.

    Used for events/sessions/channels/MCP-parity endpoints whose
    return shape is defined by the underlying tool/query function.
    The wire format is whatever ``model_extra`` allows.
    """

    model_config = {"extra": "allow"}

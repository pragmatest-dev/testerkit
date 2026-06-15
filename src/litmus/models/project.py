"""Project-level configuration types.

Schema for ``litmus.yaml`` project config files — flat, all fields at root.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from litmus.models.data_options import ChannelOptions, FileOptions
from litmus.models.test_config import PromptConfig, TestEntry


class ProfileConfig(TestEntry):
    """A named config set applied to a pytest session.

    Same flat shape as a :class:`TestEntry` (file-level Litmus-marker
    fields apply to every test in the session, recursive ``tests:``
    carries per-class / per-test overrides), plus profile-only
    ``description`` / ``facets`` / ``extends``.

    ``runner:`` is a flat per-runner config block — opaque to Litmus
    core (validated by the active runner's plugin against its own
    Pydantic schema). For pytest, fields like ``addopts``, ``markexpr``,
    ``keyword``, ``markers`` live here. One runner per session means
    one schema validates the whole block; profiles authored for a
    different runner fail fast on unknown fields.

    ``extends`` names another profile whose configuration is inherited
    and overridden last-wins by this one. Chains are walked parent-first,
    so a family / platform base can declare the shared 90% and each leaf
    profile holds only deltas. Parent profiles with no ``facets`` are
    reachable only as extends targets.
    """

    description: str | None = None
    facets: dict[str, str] = Field(default_factory=dict)
    extends: str | None = None
    # Bind a StationType this profile expects. The session-start
    # resolver verifies the active station's ``station_type`` matches
    # AND the active fixture's ``station_types`` includes this value.
    # Profiles must stay portable across stations of the matching
    # type — never bind a concrete station instance here.
    station_type: str | None = None
    # Bind a fixture by id. Resolution chain: ``--fixture`` → this
    # field → ``ProjectConfig.default_fixture``. CLI wins; explicit
    # beats declarative.
    fixture: str | None = None
    # When unset / True (default), ``verify(name, value)`` raises
    # ``MissingLimitError`` if no limit resolves from any source
    # (inline / marker / sidecar / profile / part spec). Set to
    # ``False`` on a characterization-style profile to record values
    # without judging — verify() with no resolved limit falls back to
    # ``logger.measure`` semantics (Outcome.DONE). Affects ``verify``
    # only; ``logger.measure`` is already record-only and unaffected.
    # Stored as ``bool | None`` so the profile-chain merger can tell
    # unset from explicitly-True; only ``False`` opts into the lenient
    # path.
    verify_requires_limit: bool | None = None


class MultiSlotConfig(BaseModel):
    """Multi-slot orchestration knobs.

    Surfaced as ``multi_slot:`` in ``litmus.yaml``; consumed by the
    orchestrator path that spawns one pytest child per slot.
    """

    model_config = {"extra": "forbid"}

    # Per-child grace budget after SIGTERM before the orchestrator
    # escalates to SIGKILL. Covers each child's full cleanup chain
    # (``pytest_keyboard_interrupt`` → fixture teardowns →
    # parquet flush). Bump for shops with slow instrument disconnects
    # (e.g. PyVISA timeouts on legacy GPIB hardware).
    child_grace_seconds: float = 5.0


class ProjectConfig(BaseModel):
    """Schema for litmus.yaml project config files — all fields at root."""

    model_config = {"extra": "forbid"}

    name: str
    data_dir: str | None = None
    # Per-store data options (buffering / push tuning + the files blob backend).
    channels: ChannelOptions = Field(default_factory=ChannelOptions)
    files: FileOptions = Field(default_factory=FileOptions)
    # Optional fallback station id when no ``--station`` is passed
    # and hostname auto-match doesn't fire.
    # Set this to a real station id in your project; leaving it
    # unset means session-start expects an explicit ``--station=<id>``
    # or a hostname match.
    default_station: str | None = None
    default_fixture: str | None = None
    default_profile: str | None = None
    mock_instruments: bool = False
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    runner: dict[str, Any] = Field(default_factory=dict)
    required_inputs: dict[str, PromptConfig] = Field(default_factory=dict)
    multi_slot: MultiSlotConfig = Field(default_factory=MultiSlotConfig)


# Resolve forward references — ProfileConfig inherits from TestEntry,
# so it reads MeasurementLimitConfig / MockEntry / etc. from the
# sibling module. Once that module is fully loaded, finalize the
# schema here.
ProfileConfig.model_rebuild()
ProjectConfig.model_rebuild()

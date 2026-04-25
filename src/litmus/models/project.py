"""Project-level configuration types.

Schema for ``litmus.yaml`` project config files — flat, all fields at root.
Also includes ``OutputConfig``, which describes a single entry in the
``outputs`` list of the project config.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, model_validator

from litmus.config.test_config import ConfigEntry, TestEntry
from litmus.models.config import PromptConfig


class OutputConfig(BaseModel):
    """A single output entry in the ``outputs`` list.

    Each entry specifies a format (exporter), a transport, or both:

    .. code-block:: yaml

        outputs:
          - format: html                    # report only
          - format: csv                     # export only
          - format: stdf
            transport: s3                   # export + ship
            bucket: my-results
          - transport: snowflake            # ship Parquet directly

    Extra keys (bucket, server, dsn_env, template, etc.) are passed
    through as format- or transport-specific configuration.

    Note: ``format`` and ``transport`` names are not validated against the
    registries at config time (registries are lazy-loaded). Invalid names
    will raise ``KeyError`` at runtime when the output is executed.
    """

    model_config = {"extra": "allow"}

    format: str | None = None
    transport: str | None = None
    output_dir: str | None = None
    template: str | None = None
    extras: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def _collect_extras(cls, data: Any) -> Any:
        """Collect unknown keys into extras, merging with any explicit extras dict."""
        if not isinstance(data, dict):
            return data
        known = {"format", "transport", "output_dir", "template", "extras"}
        extras = {k: v for k, v in data.items() if k not in known}
        cleaned = {k: v for k, v in data.items() if k in known}
        # Merge any explicitly provided extras
        existing = cleaned.get("extras", {})
        if isinstance(existing, dict):
            extras.update(existing)
        cleaned["extras"] = extras
        return cleaned

    @model_validator(mode="after")
    def _require_format_or_transport(self) -> OutputConfig:
        """At least one of format or transport must be set."""
        if self.format is None and self.transport is None:
            raise ValueError("OutputConfig requires at least one of 'format' or 'transport'")
        return self

    def default_output_dir(self) -> str:
        """Resolve output directory with sensible defaults.

        Subscribers own their own subfolder within the results root,
        so this just returns the root (``"results"``) for subscriber
        formats.  Report formats get ``"reports"``.
        """
        if self.output_dir:
            return self.output_dir
        if self.format in ("html", "pdf"):
            return "reports"
        return "results"


class ProfilePytest(BaseModel):
    """Pytest-level knobs a profile can apply.

    ``addopts`` is appended to ``PYTEST_ADDOPTS`` before collection so
    downstream plugins (pytest-rerunfailures, pytest-xdist, pytest-timeout)
    parse it during their own configure phase.
    """

    model_config = {"extra": "forbid"}

    addopts: str | None = None
    markexpr: str | None = None
    keyword: str | None = None


class ProfileConfig(BaseModel):
    """A named config set applied to a pytest session.

    Profiles carry config in the same recursive ``tests:`` tree as
    sidecars: file-wide ``config`` applies to every test, and ``tests:``
    holds :class:`TestEntry` nodes mirroring pytest's node-id structure.
    A class is a branch (config + nested ``tests:``); a function is a
    leaf. One vocabulary spans inline decorators, sidecar YAML, and
    profile overrides.

    ``extends`` names another profile whose configuration is inherited and
    overridden last-wins by this one. Chains are walked parent-first, so a
    family / platform base can declare the shared 90% and each leaf profile
    holds only deltas. Parent profiles with no ``facets`` are reachable
    only as extends targets.
    """

    model_config = {"extra": "forbid"}

    description: str | None = None
    facets: dict[str, str] = Field(default_factory=dict)
    extends: str | None = None
    pytest: ProfilePytest = Field(default_factory=ProfilePytest)
    config: list[ConfigEntry] = Field(default_factory=list)
    tests: dict[str, TestEntry] = Field(default_factory=dict)


class ProjectConfig(BaseModel):
    """Schema for litmus.yaml project config files — all fields at root."""

    model_config = {"extra": "forbid"}

    name: str
    results_dir: str | None = None
    default_station: str = "station"
    default_fixture: str | None = None
    default_profile: str | None = None
    mock_instruments: bool = False
    outputs: list[OutputConfig] = Field(default_factory=list)
    profiles: dict[str, ProfileConfig] = Field(default_factory=dict)
    required_inputs: dict[str, PromptConfig] = Field(default_factory=dict)

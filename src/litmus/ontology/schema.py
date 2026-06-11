"""Pydantic schema for the Litmus ontology file.

The ontology YAML at ``src/litmus/ontology/litmus.yaml`` describes every
Litmus concept, its canonical Pydantic model, and the relationships
between concepts. This module is the structural source of truth for
that file — same pattern as every other ``litmus/models/*.py``: a
typed Pydantic model validates the YAML at load time.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class VerbKind(StrEnum):
    """Closed verb vocabulary for ontology relationships.

    Adding a new verb requires updating the ontology preamble *and*
    this enum. See ``src/litmus/ontology/litmus.yaml`` for prose
    definitions of each verb.
    """

    # Domain composition — each verb carries Litmus-specific meaning.
    # See src/litmus/ontology/litmus.yaml preamble for prose definitions.
    EXPOSES = "exposes"  # Part → Pin/SignalGroup
    SPECIFIES = "specifies"  # Part → PartCharacteristic
    BUNDLES = "bundles"  # SignalGroup → BusSignal
    PARAMETERIZED_BY = "parameterized_by"  # Capability → Signal/Cond./Ctrl/Attr/Band
    EQUIPS = "equips"  # Station(Config|Type) → instruments
    OFFERS = "offers"  # CatalogEntry → channels/attrs/caps
    IDENTIFIES = "identifies"  # Asset/Record → InstrumentInfo
    CALIBRATED_PER = "calibrated_per"  # Asset/Record → CalibrationInfo
    WIRES = "wires"  # FixtureConfig/Slot → FixtureConnection
    PARTITIONS_INTO = "partitions_into"  # FixtureConfig → FixtureSlot
    ROUTED_THROUGH = "routed_through"  # FixtureConnection → SwitchRoute
    DECLARES = "declares"  # ProjectConfig → config blocks
    CONFIGURES = "configures"  # TestEntry → marker fields
    NESTS = "nests"  # TestEntry → TestEntry (recursive)
    RESOLVES_VIA = "resolves_via"  # MLC → bands/lookup/step
    RESOLVES_TO = "resolves_to"  # MLC → Limit (materialized)
    DERIVES_FROM = "derives_from"  # MLC → PartCharacteristic
    APPLIES_STIMULUS = "applies_stimulus"  # TestVector → StimulusRecord

    # Temporal containment — Session → Run → Step/CollectedItem → Vector → Measurement.
    CONTAINS = "contains"

    # Class / type-instance
    INHERITS_FROM = "inherits_from"  # Pydantic class inheritance
    INSTANCE_OF = "instance_of"  # UUT instance_of Part
    INSTANTIATED_AS = "instantiated_as"  # Part instantiated_as UUT (inverse)

    # Naming pointers (FK by id) — used when no domain verb fits
    REFERENCES = "references"  # plain id pointer
    EXTENDS = "extends"  # YAML profile-chain merge (last-wins)

    # Execution semantics
    TESTS = "tests"  # TestRun tests UUT
    RUNS_ON = "runs_on"  # TestRun runs_on Station
    VALIDATES_AGAINST = "validates_against"  # StationConfig validates_against StationType

    # Event semantics
    EMITS = "emits"  # Entity emits Event
    RECORDS = "records"  # Event records Entity state/occurrence
    PAIRED_WITH = "paired_with"  # Start/end event pair

    # Config layering
    OVERLAYS = "overlays"  # Sidecar/Marker overlays pytest test function

    # Storage
    STORED_IN = "stored_in"  # Concept stored in a Store


class Category(StrEnum):
    """Concept category — drives rendering and filtering."""

    DEFINITION = "definition"
    PRIMITIVE = "primitive"
    CONFIG_OVERLAY = "config-overlay"
    RUNTIME = "runtime"
    EVENT = "event"
    STORE = "store"
    ENUM = "enum"
    LIFECYCLE = "lifecycle"
    EXTERNAL = "external"


class Relationship(BaseModel):
    """One typed edge from a concept to another concept by id."""

    model_config = ConfigDict(extra="forbid")

    kind: VerbKind
    target: str


class Concept(BaseModel):
    """One node in the ontology graph."""

    model_config = ConfigDict(extra="forbid")

    id: str
    category: Category
    summary: str
    model: str | None = None
    authored_at: str | None = None
    event_type: str | None = None
    docs: str | None = None
    docs_extra: str | None = None
    relationships: list[Relationship] = Field(default_factory=list)


class Slice(BaseModel):
    """A named subgraph view rendered as a Mermaid diagram."""

    model_config = ConfigDict(extra="forbid")

    id: str
    title: str
    summary: str
    layout: Literal["LR", "TB", "BT", "RL"] = "LR"
    concepts: list[str]
    highlight: list[str] = Field(default_factory=list)
    # None means "include every verb"; a list restricts edges to those verbs.
    edges: list[VerbKind] | None = None


class IgnoredEntry(BaseModel):
    """A Pydantic model intentionally absent from the ontology, with reason."""

    model_config = ConfigDict(extra="forbid")

    model: str
    reason: str


class Ontology(BaseModel):
    """Root model for ``src/litmus/ontology/litmus.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: int
    concepts: list[Concept]
    slices: list[Slice] = Field(default_factory=list)
    ontology_ignored: list[IgnoredEntry] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_internal_consistency(self) -> Ontology:
        ids = {c.id for c in self.concepts}

        # Duplicate concept ids
        if len(ids) != len(self.concepts):
            seen: set[str] = set()
            dupes: set[str] = set()
            for c in self.concepts:
                if c.id in seen:
                    dupes.add(c.id)
                seen.add(c.id)
            raise ValueError(f"duplicate concept ids: {sorted(dupes)}")

        # Relationship targets must resolve
        unresolved: list[str] = []
        for c in self.concepts:
            for rel in c.relationships:
                if rel.target not in ids:
                    unresolved.append(f"{c.id} --{rel.kind.value}--> {rel.target}")
        if unresolved:
            raise ValueError(
                "relationships point to unknown concept ids:\n  " + "\n  ".join(unresolved)
            )

        # Slice members and highlights must be known concepts
        slice_errors: list[str] = []
        for s in self.slices:
            for cid in s.concepts:
                if cid not in ids:
                    slice_errors.append(f"slice {s.id!r} lists unknown concept {cid!r}")
            for cid in s.highlight:
                if cid not in s.concepts:
                    slice_errors.append(
                        f"slice {s.id!r} highlights {cid!r} but does not include it in concepts"
                    )
        if slice_errors:
            raise ValueError("slice errors:\n  " + "\n  ".join(slice_errors))

        return self

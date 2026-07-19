"""Instrument catalog entry model.

Defines the schema for structured capability data for real instruments.
Catalog entries describe what a specific make/model of instrument can do,
independent of any particular project, driver, or station configuration.

3-tier architecture:
    catalog/keysight_34461a.yaml       <- Universal: "what can this MODEL do"
    instruments/dmm_bench_001.yaml     <- Unit-specific: serial, calibration, catalog_ref
    stations/bench_01.yaml             <- Project-local: role, driver, resource
"""

from pydantic import BaseModel, Field

from testerkit.models.capability import Attribute, ChannelTopology, InstrumentCapability

__all__ = ["InstrumentCatalogEntry"]


class InstrumentCatalogEntry(BaseModel):
    """Structured capability data for a specific instrument make/model.

    This is the universal tier — it describes what an instrument MODEL can do,
    not what a specific unit is or where it lives.

    The optional ``driver`` field is a catalog-level default for the instrument
    driver class (e.g., ``"pymeasure.instruments.keithley.Keithley2400"``).
    Station config can override it; if absent there, the catalog value is used
    as a fallback by the instrument pool loader.

    Channels use structured ``ChannelTopology`` dicts describing physical
    terminals, connector types, and ground topology.

    Example YAML:
        id: keysight_34461a
        manufacturer: Keysight
        model: "34461A"
        name: "Keysight 34461A Digital Multimeter"
        type: dmm
        interfaces: [usb, lan, gpib]
        channels:
          "1":
            terminals: [hi, lo]
            connector: binding_post
            ground: shared
        capabilities:
          - function: dc_voltage
            direction: input
            signals:
              voltage:
                range: {min: 0.0001, max: 1000, unit: V}
                accuracy: {pct_reading: 0.0035, pct_range: 0.0006}
                resolution: {digits: 6.5}
    """

    model_config = {"extra": "forbid"}

    id: str
    manufacturer: str
    model: str
    name: str | None = None  # Defaults to "{manufacturer} {model}" in loader
    description: str | None = None
    type: str  # e.g. "dmm", "psu", "scope", "fgen", "smu", "eload"
    base: str | None = None  # Variant inheritance: resolved at load time by store.py
    scaffold: bool = False  # True = approximate entry, needs verification
    driver: str | None = None  # e.g. "pymeasure.instruments.keithley.Keithley2400"
    interfaces: list[str] = Field(default_factory=list)
    form_factor: str | None = None
    channels: dict[str, ChannelTopology] = Field(default_factory=dict)
    attributes: dict[str, Attribute] = Field(default_factory=dict)
    capabilities: list[InstrumentCapability] = Field(default_factory=list)

    @property
    def channel_names(self) -> list[str]:
        """Channel key names."""
        return list(self.channels.keys())

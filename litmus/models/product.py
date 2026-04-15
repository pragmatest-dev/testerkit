"""Pydantic models for product specifications.

This module defines the information model for products (DUTs) such that specs
flow down to:
1. Required capabilities (what instruments are needed)
2. Station config (which instruments at which addresses)
3. Test limits (derived from specs with guardbands)
4. Test code (what to measure, what limits to apply)
5. Results (full traceability back to specs)

Key design principle: Product characteristics and instrument capabilities share
the same base model (Capability) with MeasurementFunction, Direction,
and typed parameter dicts (signals, conditions, controls, attributes).
This enables direct capability matching without lossy conversion — direction
pairing lives in the matching service.
"""

from enum import StrEnum
from typing import Any, Self

from pydantic import BaseModel, Field, computed_field, model_validator

from litmus.config.capability import band_matches
from litmus.models.config import (
    Capability,
    SpecBand,
)
from litmus.utils.ranges import expand_range


class PinRole(StrEnum):
    """Role of a physical DUT pin in the test system.

    Distinguishes signal, ground, power, and reference pins so the
    auto-match algorithm can route them correctly (e.g., ground pins
    fan out to instrument LO terminals instead of competing for
    measurement channels).
    """

    SIGNAL = "signal"       # Measured/stimulated signal
    GROUND = "ground"       # Current return / reference
    POWER = "power"         # Power input/output (VIN, VOUT)
    REFERENCE = "reference" # Voltage reference, not driven


class Pin(BaseModel):
    """Physical pin/pad on the DUT (ATML: Port).

    Represents a single connection point that can be routed through
    a fixture to an instrument. The ``role`` field classifies the pin's
    purpose (signal, ground, power, reference) for fixture routing.

    Example YAML:
        pins:
          VIN:
            name: "J1.1"
            net: "VIN_5V"
            role: power
            description: "5V power input from bench supply"
          VOUT:
            name: "J1.3"
            net: "VOUT_3V3"
            description: "3.3V regulated output"
          GND:
            name: "J1.2"
            net: "GND"
            role: ground
    """

    model_config = {"extra": "forbid"}

    name: str  # Pin designator: "J1.1", "TP5", "U3.14"
    net: str | None = None  # Schematic net name
    role: PinRole = PinRole.SIGNAL  # Pin role for routing
    description: str | None = None


class BusSignal(BaseModel):
    """A signal within a bus group.

    Example YAML:
        signals:
          - pin: SDA
            role: data
          - pin: SCL
            role: clock
    """

    model_config = {"extra": "forbid"}

    pin: str  # Reference to Pin key
    role: str  # "clock", "data", "chip_select", "strobe", etc.
    index: int | None = None  # For multi-bit: DATA[0], DATA[1]


class SignalGroup(BaseModel):
    """Grouped signals forming a bus interface (ATML: Bus).

    Used for protocols like I2C, SPI, UART where multiple signals
    must be treated as a unit for routing and testing.

    Example YAML:
        signal_groups:
          i2c_main:
            protocol: i2c
            signals:
              - pin: SDA
                role: data
              - pin: SCL
                role: clock
            parameters:
              frequency: 400000
    """

    model_config = {"extra": "forbid"}

    protocol: str  # "i2c", "spi", "uart", "parallel", "custom"
    signals: list[BusSignal] = Field(default_factory=list)
    parameters: dict[str, Any] = Field(default_factory=dict)
    description: str | None = None


class ProductCharacteristic(Capability):
    """Product capability + physical interface + traceability (ATML: UUT Characteristic).

    Extends Capability with product-specific fields: physical pin mapping,
    net names, signal groups, and datasheet references.

    The direction indicates whether the DUT provides (OUTPUT) or consumes (INPUT)
    this signal. Direction pairing for matching (DUT OUTPUT → instrument INPUT)
    lives in the matching service, not here.

    REQUIRES physical interface: Every characteristic must be tied to at least one
    physical connection point (pin, net, or signal_group).

    Example YAML:
        rail_3v3_output:
          function: dc_voltage
          direction: output
          units: V
          pin: VOUT
          parameters:
            voltage:
              value: 3.3
              units: V
          specs:
            - conditions:
                temperature: {min: 25, max: 25, units: degC}
                load: {min: 0.1, max: 0.1, units: A}
              value: 3.3
              accuracy: {pct_reading: 3.0}
    """

    # Physical interface - AT LEAST ONE REQUIRED
    pin: str | None = None  # Single pin reference (Product.pins key)
    pins: str | list[str] = Field(default_factory=list)  # Multiple pins (range: "GPIO[0:7]")
    net: str | None = None  # Schematic net name (matches fixture routing)
    signal_group: str | None = None  # Reference to Product.signal_groups key

    # Traceability
    datasheet_ref: str | None = None

    @computed_field
    @property
    def resolved_pins(self) -> list[str]:
        """Expand pins to list, handling range syntax.

        Supports:
        - Single pin: pin="TP_VOUT" → ["TP_VOUT"]
        - Explicit list: pins=["GPIO0", "GPIO1"] → ["GPIO0", "GPIO1"]
        - Range string: pins="GPIO[0:7]" → ["GPIO0", "GPIO1", ..., "GPIO7"]
        - Non-contiguous: pins="GPIO[0,2,4:6]" → ["GPIO0", "GPIO2", "GPIO4", "GPIO5", "GPIO6"]
        """
        if self.pin:
            return [self.pin]
        if self.pins:
            return expand_range(self.pins)
        return []

    @model_validator(mode="after")
    def validate_physical_interface(self) -> Self:
        """Ensure characteristic is tied to physical interface.

        Every characteristic must specify WHERE on the DUT it applies.
        This enables fixture mapping and signal routing.
        """
        has_interface = any([
            self.pin,
            self.pins,
            self.net,
            self.signal_group,
        ])
        if not has_interface:
            raise ValueError(
                "Characteristic must specify physical interface: "
                "pin, pins, net, or signal_group"
            )
        return self

    def get_spec_at(self, params: dict[str, float | str | bool]) -> SpecBand | None:
        """Find the SpecBand matching the given operating point.

        Args:
            params: Dictionary of condition parameters (e.g., {"temperature": 25}).
                Each value is checked against the RangeSpec in the band's ``when`` clause.

        Returns:
            The matching SpecBand, or None if no match found.
        """
        for band in self.specs:
            if band_matches(band, params):
                return band
        return None


class Product(BaseModel):
    """Product definition (ATML: UUT Description).

    Top-level model that ties together:
    - Product identification (id, name, revision)
    - Documentation references (datasheet, schematic)
    - Characteristics with condition matrices

    Example YAML:
        product:
          id: power_board_v1
          name: "DC-DC Power Board Rev A"
          revision: "A"
          datasheet: "docs/DS-001.pdf"

        characteristics:
          rail_3v3_output:
            function: dc_voltage
            direction: output
            units: V
            pin: VOUT
            specs:
              - value: 3.3
                accuracy: {pct_reading: 3.0}
    """

    model_config = {"extra": "forbid"}

    id: str
    name: str
    part_number: str | None = None
    base: str | None = None
    description: str | None = None
    revision: str | None = None
    datasheet: str | None = None
    schematic: str | None = None
    driver: str | None = None  # Dotted import path (e.g., "drivers.my_board.MyBoard")

    # Physical interface (ATML: UUT Ports)
    pins: dict[str, Pin] = Field(default_factory=dict)
    signal_groups: dict[str, SignalGroup] = Field(default_factory=dict)

    # Electrical characteristics
    characteristics: dict[str, ProductCharacteristic] = Field(default_factory=dict)

    def get_pins_by_net(self, net: str) -> list[str]:
        """Return pin IDs whose net matches the given name."""
        return [pid for pid, pin in self.pins.items() if pin.net == net]

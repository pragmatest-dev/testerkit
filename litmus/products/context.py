"""Spec context for spec-driven testing.

The SpecContext bridges product specifications and test execution by:
1. Loading and holding the product spec
2. Providing limit derivation from characteristics
3. Tracking channel/pin mapping for measurement traceability
"""

from __future__ import annotations

from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING, Any

from litmus.config.models import Limit
from litmus.products.limits import derive_limit
from litmus.products.loader import load_product
from litmus.products.models import Characteristic, Product, TestRequirement

if TYPE_CHECKING:
    from litmus.config.models import FixtureConfig


class SpecContext:
    """Context for spec-driven testing.

    Holds product spec and provides methods to derive limits and track
    channel traceability through the test execution.

    Example usage:
        spec = SpecContext.from_file("products/power_board/spec.yaml")

        # Get limit for a characteristic at specific conditions
        limit = spec.get_limit("output_voltage", temperature=25, load=0.1)

        # Get pin info for traceability
        pin_info = spec.get_pin_info("output_voltage")
    """

    def __init__(
        self,
        product: Product,
        fixture: FixtureConfig | None = None,
        guardband_pct: Decimal = Decimal("0"),
    ):
        """Initialize spec context.

        Args:
            product: Loaded Product specification.
            fixture: Optional fixture config for channel routing.
            guardband_pct: Default guardband percentage for all limits.
        """
        self.product = product
        self.fixture = fixture
        self.default_guardband_pct = guardband_pct

        # Build lookup caches
        self._char_by_pin: dict[str, list[str]] = {}  # pin -> [char_ids]
        self._pin_by_char: dict[str, list[str]] = {}  # char_id -> [pins]

        for char_id, char in product.characteristics.items():
            # Collect all pins for this characteristic (pin, pins, or net lookup)
            all_pins = self._get_char_pins(char)
            self._pin_by_char[char_id] = all_pins
            for pin in all_pins:
                if pin not in self._char_by_pin:
                    self._char_by_pin[pin] = []
                self._char_by_pin[pin].append(char_id)

    def _get_char_pins(self, char: Characteristic) -> list[str]:
        """Get all pin references for a characteristic.

        Uses resolved_pins computed property which handles:
        - char.pin: Single pin reference
        - char.pins: List or range string (e.g., "GPIO[0:7]")
        - char.net: Net name (looked up in product.pins)
        """
        # Use computed property that handles range expansion
        pins = list(char.resolved_pins)

        # Net reference - find matching pin by net name
        if char.net and not pins:
            for pin_id, pin in self.product.pins.items():
                if pin.net == char.net:
                    pins.append(pin_id)
                    break

        return pins

    @classmethod
    def from_file(
        cls,
        spec_path: str | Path,
        fixture: FixtureConfig | None = None,
        guardband_pct: Decimal = Decimal("0"),
    ) -> SpecContext:
        """Load spec context from YAML file.

        Args:
            spec_path: Path to product spec YAML.
            fixture: Optional fixture config.
            guardband_pct: Default guardband percentage.

        Returns:
            Initialized SpecContext.
        """
        product = load_product(Path(spec_path))
        return cls(product, fixture, guardband_pct)

    def get_characteristic(self, char_id: str) -> Characteristic | None:
        """Get a characteristic by ID."""
        return self.product.characteristics.get(char_id)

    def get_limit(
        self,
        char_id: str,
        guardband_pct: Decimal | None = None,
        **conditions: Any,
    ) -> Limit:
        """Derive test limit from a characteristic.

        Args:
            char_id: Characteristic ID (e.g., "output_voltage").
            guardband_pct: Override guardband (uses default if None).
            **conditions: Condition parameters (e.g., temperature=25, load=0.1).

        Returns:
            Limit with derived low/high bounds and spec_ref.

        Raises:
            KeyError: If characteristic not found.
            ValueError: If no condition matches.
        """
        char = self.product.characteristics.get(char_id)
        if char is None:
            raise KeyError(f"Characteristic '{char_id}' not found in product '{self.product.id}'")

        # Create synthetic TestRequirement for limit derivation
        gb_pct = guardband_pct if guardband_pct is not None else self.default_guardband_pct
        req = TestRequirement(
            characteristic_ref=char_id,
            conditions=conditions,
            guardband_pct=gb_pct,
        )

        return derive_limit(char, req, conditions if conditions else None)

    def get_limit_from_requirement(self, req_id: str) -> Limit:
        """Derive limit from a test requirement.

        Args:
            req_id: Test requirement ID.

        Returns:
            Limit derived from the requirement's characteristic.

        Raises:
            KeyError: If requirement or referenced characteristic not found.
        """
        req = self.product.test_requirements.get(req_id)
        if req is None:
            raise KeyError(f"Test requirement '{req_id}' not found")

        if req.characteristic_ref is None:
            raise ValueError(f"Test requirement '{req_id}' has no characteristic_ref")

        char = self.product.characteristics.get(req.characteristic_ref)
        if char is None:
            raise KeyError(f"Characteristic '{req.characteristic_ref}' not found")

        return derive_limit(char, req)

    def get_pin_info(self, char_id: str) -> dict[str, Any]:
        """Get pin information for a characteristic.

        Returns info useful for measurement traceability.

        Args:
            char_id: Characteristic ID.

        Returns:
            Dict with pin details:
                - pin: Primary pin ID (from char.pin or first of char.pins)
                - pins: List of all pin IDs
                - dut_pin: Physical pin name (from Pin.name)
                - net: Schematic net name (from primary pin)
                - fixture_point: Fixture channel name (if fixture configured)
                - instrument_channel: Instrument channel (if fixture configured)
        """
        char = self.product.characteristics.get(char_id)
        if char is None:
            return {}

        # Get all pins for this characteristic
        all_pins = self._get_char_pins(char)

        result: dict[str, Any] = {
            "pin": char.pin or (all_pins[0] if all_pins else None),
            "pins": all_pins,
            "dut_pin": None,
            "net": char.net,  # Direct net reference
            "fixture_point": None,
            "instrument_channel": None,
        }

        # Get primary pin info
        if all_pins:
            primary_pin_id = all_pins[0]
            pin = self.product.pins.get(primary_pin_id)
            if pin:
                result["dut_pin"] = pin.name
                result["net"] = pin.net

                # Look up fixture routing if available
                if self.fixture:
                    for ch_name, ch in self.fixture.channels.items():
                        if ch.dut_pin == primary_pin_id or ch.net == pin.net:
                            result["fixture_point"] = ch_name
                            result["instrument_channel"] = ch.instrument_channel
                            break

        return result

    def get_all_characteristics_for_pin(self, pin_id: str) -> list[str]:
        """Get all characteristic IDs that reference a pin.

        Useful for finding what tests apply to a given DUT pin.

        Args:
            pin_id: Pin ID (key in product.pins).

        Returns:
            List of characteristic IDs.
        """
        return self._char_by_pin.get(pin_id, [])

    def list_characteristics(self) -> list[str]:
        """List all characteristic IDs."""
        return list(self.product.characteristics.keys())

    def list_test_requirements(self) -> list[str]:
        """List all test requirement IDs."""
        return list(self.product.test_requirements.keys())

    def list_pins(self) -> list[str]:
        """List all pin IDs."""
        return list(self.product.pins.keys())

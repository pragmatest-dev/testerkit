"""Spec context for spec-driven testing.

The PartContext bridges part specifications and test execution by:
1. Loading and holding the part spec
2. Providing limit derivation from characteristics
3. Tracking channel/pin mapping for measurement traceability
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from litmus.models.enums import Comparator
from litmus.models.part import Part, PartCharacteristic
from litmus.models.test_config import Limit

if TYPE_CHECKING:
    from litmus.models.test_config import FixtureConfig


class PartContext:
    """Context for spec-driven testing.

    Holds part spec and provides methods to derive limits and track
    channel traceability through the test execution.

    Example usage:
        spec = PartContext.from_file("parts/power_board.yaml")

        # Get limit for a characteristic at specific conditions
        limit = spec.get_limit("output_voltage", temperature=25, load=0.1)

        # Get pin info for traceability
        pin_info = spec.get_pin_info("output_voltage")
    """

    def __init__(
        self,
        part: Part,
        fixture: FixtureConfig | None = None,
        guardband_pct: float = 0.0,
    ):
        """Initialize spec context.

        Args:
            part: Loaded Part specification.
            fixture: Optional fixture config for channel routing.
            guardband_pct: Default guardband percentage for all limits.
        """
        self.part = part
        self.fixture = fixture
        self.default_guardband_pct = guardband_pct

        # Build lookup cache: pin -> [char_ids]
        self._char_by_pin: dict[str, list[str]] = {}

        for char_id, char in part.characteristics.items():
            all_pins = self._get_char_pins(char)
            for pin in all_pins:
                if pin not in self._char_by_pin:
                    self._char_by_pin[pin] = []
                self._char_by_pin[pin].append(char_id)

    def _get_char_pins(self, char: PartCharacteristic) -> list[str]:
        """Get all pin references for a characteristic."""
        pins = list(char.resolved_pins)

        # Net reference - find matching pin by net name
        if char.net and not pins:
            net_pins = self.part.get_pins_by_net(char.net)
            if net_pins:
                pins.append(net_pins[0])

        return pins

    @classmethod
    def from_file(
        cls,
        spec_path: str | Path,
        fixture: FixtureConfig | None = None,
        guardband_pct: float = 0.0,
    ) -> PartContext:
        """Load spec context from YAML file."""
        from litmus.store import load_part

        part = load_part(Path(spec_path))
        return cls(part, fixture, guardband_pct)

    def get_limit(
        self,
        char_id: str,
        guardband_pct: float | None = None,
        comparator: Comparator | None = None,
        limit_low: float | None = None,
        limit_high: float | None = None,
        **conditions: Any,
    ) -> Limit:
        """Derive test limit from a characteristic.

        Args:
            char_id: PartCharacteristic ID (e.g., "output_voltage").
            guardband_pct: Override guardband (uses default if None).
            comparator: Override comparator (defaults to GELE).
            limit_low: Explicit low limit override.
            limit_high: Explicit high limit override.
            **conditions: Condition parameters (e.g., temperature=25, load=0.1).

        Returns:
            Limit with derived low/high bounds, characteristic_id, and spec_ref.

        Raises:
            KeyError: If characteristic not found.
        """
        char = self.part.characteristics.get(char_id)
        if char is None:
            raise KeyError(f"PartCharacteristic '{char_id}' not found in part '{self.part.id}'")

        from litmus.execution.limits import derive_limit

        gb_pct = guardband_pct if guardband_pct is not None else self.default_guardband_pct
        cmp = comparator or Comparator.GELE

        return derive_limit(
            char,
            conditions=conditions if conditions else None,
            guardband_pct=gb_pct,
            comparator=cmp,
            limit_low=limit_low,
            limit_high=limit_high,
            char_id=char_id,
        )

    def get_pin_info(self, char_id: str) -> dict[str, Any]:
        """Get pin information for a characteristic.

        Returns info useful for measurement traceability.
        """
        char = self.part.characteristics.get(char_id)
        if char is None:
            return {}

        all_pins = self._get_char_pins(char)

        result: dict[str, Any] = {
            "pin": char.pin or (all_pins[0] if all_pins else None),
            "pins": all_pins,
            "dut_pin": None,
            "net": char.net,
            "fixture_connection": None,
            "instrument_channel": None,
        }

        if all_pins:
            primary_pin_id = all_pins[0]
            pin = self.part.pins.get(primary_pin_id)
            if pin:
                result["dut_pin"] = pin.name
                if pin.net is not None:
                    result["net"] = pin.net

                if self.fixture:
                    for fc_name, fc in self.fixture.connections.items():
                        if fc.dut_pin == primary_pin_id or fc.net == pin.net:
                            result["fixture_connection"] = fc_name
                            result["instrument_channel"] = fc.instrument_channel
                            break

        return result

    def get_all_characteristics_for_pin(self, pin_id: str) -> list[str]:
        """Get all characteristic IDs that reference a pin."""
        return self._char_by_pin.get(pin_id, [])

    def list_characteristics(self) -> list[str]:
        """List all characteristic IDs."""
        return list(self.part.characteristics.keys())

    def list_pins(self) -> list[str]:
        """List all pin IDs."""
        return list(self.part.pins.keys())

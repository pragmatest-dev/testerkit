"""Pluggable DUT identity providers for multi-slot fixtures.

A DUTProvider resolves device-under-test identity for each fixture slot.
Built-in providers handle CLI args and environment variables. Users can
implement the protocol for MES systems, barcode scanners, etc.
"""

import os
from typing import Protocol, runtime_checkable

from litmus.data.models import DUT


class DUTProviderError(ValueError):
    """Error resolving DUT identity for a slot."""


@runtime_checkable
class DUTProvider(Protocol):
    """Protocol for resolving DUT identity per slot."""

    def get_dut(self, slot_id: str) -> DUT:
        """Return DUT identity for the given slot.

        Args:
            slot_id: Fixture slot identifier (e.g., "slot_1").

        Returns:
            DUT with at least ``serial`` populated.

        Raises:
            ValueError: If no DUT identity is available for the slot.
        """
        ...


class CLIDUTProvider:
    """Resolves DUT identity from CLI arguments.

    Supports three modes:
    - Single serial: applied to all slots (``--dut-serial SN001``)
    - Named per-slot: ``--dut-serials slot_1=SN001,slot_2=SN002``
    - Positional per-slot: ``--dut-serials SN001,SN002`` (maps to slots in order)

    Part number, revision, and lot number are shared across all slots
    (they come from the same product).
    """

    def __init__(
        self,
        *,
        serial: str | None = None,
        serials: dict[str, str] | None = None,
        part_number: str | None = None,
        revision: str | None = None,
        lot_number: str | None = None,
    ) -> None:
        if serial and serials:
            raise DUTProviderError(
                "Specify either 'serial' (all slots) or 'serials' (per-slot), not both"
            )
        if not serial and not serials:
            raise DUTProviderError("Either 'serial' or 'serials' must be provided")

        self._serial = serial
        self._serials = serials or {}
        self._part_number = part_number
        self._revision = revision
        self._lot_number = lot_number

    def get_dut(self, slot_id: str) -> DUT:
        """Return DUT for slot. Single serial applies to all slots."""
        if self._serial:
            serial = self._serial
        elif slot_id in self._serials:
            serial = self._serials[slot_id]
        else:
            available = ", ".join(sorted(self._serials))
            raise DUTProviderError(
                f"No DUT serial for slot '{slot_id}'. "
                f"Available slots: {available}"
            )

        return DUT(
            serial=serial,
            part_number=self._part_number,
            revision=self._revision,
            lot_number=self._lot_number,
        )

    @staticmethod
    def parse_serials(
        raw: str, slot_ids: list[str] | None = None,
    ) -> dict[str, str]:
        """Parse a ``--dut-serials`` string into a slot→serial dict.

        Supports two formats (auto-detected):
        - **Named:** ``slot_1=SN001,slot_2=SN002``
        - **Positional:** ``SN001,SN002`` (requires ``slot_ids`` for mapping)

        Args:
            raw: Raw CLI string.
            slot_ids: Ordered slot IDs from fixture config. Required for
                positional format; ignored for named format.

        Returns:
            Dict mapping slot_id → serial.
        """
        parts = [p.strip() for p in raw.split(",")]

        # Auto-detect: if any part has '=', treat all as named
        if any("=" in p for p in parts):
            serials: dict[str, str] = {}
            for part in parts:
                if "=" not in part:
                    raise DUTProviderError(
                        f"Invalid --dut-serials format: '{part}'. "
                        f"Expected 'slot_id=serial' pairs."
                    )
                slot, serial = part.split("=", 1)
                serials[slot.strip()] = serial.strip()
            return serials

        # Positional: need slot_ids to map
        if slot_ids is None:
            raise DUTProviderError(
                "Positional --dut-serials (no slot= prefix) requires a "
                "multi-slot fixture config to determine slot order."
            )
        if len(parts) != len(slot_ids):
            raise DUTProviderError(
                f"--dut-serials has {len(parts)} serial(s) but fixture "
                f"has {len(slot_ids)} slot(s): {', '.join(slot_ids)}"
            )
        return dict(zip(slot_ids, parts))

    @classmethod
    def from_cli_args(
        cls,
        dut_serial: str | None,
        dut_serials: str | None,
        *,
        slot_ids: list[str] | None = None,
        part_number: str | None = None,
        revision: str | None = None,
        lot_number: str | None = None,
    ) -> "CLIDUTProvider":
        """Create from pytest CLI option values.

        Args:
            dut_serial: Single serial (``--dut-serial``).
            dut_serials: Comma-separated serials, either named
                (``slot_1=SN1,slot_2=SN2``) or positional (``SN1,SN2``).
            slot_ids: Ordered slot IDs from fixture config. Required when
                using positional ``--dut-serials``.
        """
        if dut_serials:
            serials_dict = cls.parse_serials(dut_serials, slot_ids)
            return cls(
                serials=serials_dict,
                part_number=part_number,
                revision=revision,
                lot_number=lot_number,
            )

        return cls(
            serial=dut_serial or "DUT001",
            part_number=part_number,
            revision=revision,
            lot_number=lot_number,
        )


class EnvironmentDUTProvider:
    """Resolves DUT identity from environment variables.

    Variable naming convention:
    - ``LITMUS_DUT_SERIAL`` — single serial for all slots
    - ``LITMUS_DUT_SERIAL_SLOT_1`` — per-slot serial (slot_id uppercased)
    - ``LITMUS_DUT_PART_NUMBER`` — shared part number
    - ``LITMUS_DUT_REVISION`` — shared revision
    - ``LITMUS_DUT_LOT_NUMBER`` — shared lot number
    """

    def get_dut(self, slot_id: str) -> DUT:
        """Return DUT from environment variables."""
        # Try slot-specific first, then global
        env_key = f"LITMUS_DUT_SERIAL_{slot_id.upper()}"
        serial = os.environ.get(env_key) or os.environ.get("LITMUS_DUT_SERIAL")

        if not serial:
            raise DUTProviderError(
                f"No DUT serial in environment for slot '{slot_id}'. "
                f"Set {env_key} or LITMUS_DUT_SERIAL."
            )

        return DUT(
            serial=serial,
            part_number=os.environ.get("LITMUS_DUT_PART_NUMBER"),
            revision=os.environ.get("LITMUS_DUT_REVISION"),
            lot_number=os.environ.get("LITMUS_DUT_LOT_NUMBER"),
        )

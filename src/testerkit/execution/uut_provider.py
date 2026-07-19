"""Pluggable UUT identity providers for multi-site fixtures.

A UUTProvider resolves device-under-test identity for each fixture site.
Built-in providers handle CLI args and environment variables. Users can
implement the protocol for MES systems, barcode scanners, etc.
"""

import os
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from testerkit.data.models import UUT
from testerkit.execution.sites import resolve_site_token

if TYPE_CHECKING:
    from testerkit.execution.sites import ResolvedSite


class UUTProviderError(ValueError):
    """Error resolving UUT identity for a site."""


@runtime_checkable
class UUTProvider(Protocol):
    """Protocol for resolving UUT identity per site."""

    def get_uut(self, site_index: int) -> UUT:
        """Return UUT identity for the given site.

        Args:
            site_index: 0-based fixture site index.

        Returns:
            UUT with at least ``serial`` populated.

        Raises:
            ValueError: If no UUT identity is available for the site.
        """
        ...


class CLIUUTProvider:
    """Resolves UUT identity from CLI arguments.

    Supports three modes:
    - Single serial: applied to all sites (``--uut-serial SN001``)
    - Named per-site: ``--uut-serials left=SN001,right=SN002`` (by site name)
    - Indexed per-site: ``--uut-serials 0=SN001,1=SN002`` (by site_index)
    - Positional per-site: ``--uut-serials SN001,SN002`` (maps to sites in order)

    Part number, revision, and lot number are shared across all sites
    (they come from the same part).
    """

    def __init__(
        self,
        *,
        serial: str | None = None,
        serials: dict[int, str] | None = None,
        part_number: str | None = None,
        revision: str | None = None,
        lot_number: str | None = None,
    ) -> None:
        if serial and serials:
            raise UUTProviderError(
                "Specify either 'serial' (all sites) or 'serials' (per-site), not both"
            )
        if not serial and not serials:
            raise UUTProviderError("Either 'serial' or 'serials' must be provided")

        self._serial = serial
        self._serials: dict[int, str] = serials or {}
        self._part_number = part_number
        self._revision = revision
        self._lot_number = lot_number

    def get_uut(self, site_index: int) -> UUT:
        """Return UUT for site. Single serial applies to all sites."""
        if self._serial:
            serial = self._serial
        elif site_index in self._serials:
            serial = self._serials[site_index]
        else:
            available = ", ".join(str(i) for i in sorted(self._serials))
            raise UUTProviderError(
                f"No UUT serial for site {site_index}. Available site indices: {available}"
            )

        return UUT(
            serial=serial,
            part_number=self._part_number,
            revision=self._revision,
            lot_number=self._lot_number,
        )

    @staticmethod
    def parse_serials(
        raw: str,
        sites: list["ResolvedSite"] | None = None,
    ) -> dict[int, str]:
        """Parse a ``--uut-serials`` string into a site_index→serial dict.

        Supports three formats (auto-detected):
        - **Indexed:** ``0=SN001,1=SN002``
        - **Named:** ``left=SN001,right=SN002`` (resolved via site names)
        - **Positional:** ``SN001,SN002`` (requires ``sites`` for mapping)

        For the named format, the key is resolved first as an integer
        (site_index); if that fails, it is matched against site names.

        Args:
            raw: Raw CLI string.
            sites: Resolved fixture sites. Required for positional format and
                for resolving name-based keys.

        Returns:
            Dict mapping site_index → serial.

        Raises:
            UUTProviderError: A key is malformed, doesn't resolve to a
                known site, or two keys resolve to the same
                ``site_index`` (bijection violation — see the
                site-model contract's "no site fed twice" rule).
        """
        parts = [p.strip() for p in raw.split(",")]

        # Auto-detect: if any part has '=', treat all as keyed
        if any("=" in p for p in parts):
            serials: dict[int, str] = {}
            for part in parts:
                if "=" not in part:
                    raise UUTProviderError(
                        f"Invalid --uut-serials format: '{part}'. "
                        "Expected 'index=serial' or 'name=serial' pairs."
                    )
                key, serial = part.split("=", 1)
                key = key.strip()
                serial = serial.strip()

                try:
                    site_index = resolve_site_token(key, sites or [])
                except ValueError as exc:
                    raise UUTProviderError(str(exc)) from exc

                if site_index in serials:
                    raise UUTProviderError(
                        f"--uut-serials key '{key}' resolves to site_index "
                        f"{site_index}, already assigned serial "
                        f"{serials[site_index]!r} by an earlier key."
                    )
                serials[site_index] = serial
            return serials

        # Positional: need sites to map
        if sites is None:
            raise UUTProviderError(
                "Positional --uut-serials (no index= prefix) requires a "
                "multi-site fixture config to determine site order."
            )
        if len(parts) != len(sites):
            raise UUTProviderError(
                f"--uut-serials has {len(parts)} serial(s) but fixture has {len(sites)} site(s)"
            )
        return {site.site_index: serial for site, serial in zip(sites, parts)}

    @classmethod
    def from_cli_args(
        cls,
        uut_serial: str | None,
        uut_serials: str | None,
        *,
        sites: list["ResolvedSite"] | None = None,
        part_number: str | None = None,
        revision: str | None = None,
        lot_number: str | None = None,
    ) -> "CLIUUTProvider":
        """Create from pytest CLI option values.

        Args:
            uut_serial: Single serial (``--uut-serial``).
            uut_serials: Comma-separated serials, either keyed
                (``0=SN1,1=SN2`` or ``left=SN1,right=SN2``) or
                positional (``SN1,SN2``).
            sites: Resolved fixture sites. Required when
                using positional ``--uut-serials`` or name keys.
        """
        if uut_serials:
            serials_dict = cls.parse_serials(uut_serials, sites)
            return cls(
                serials=serials_dict,
                part_number=part_number,
                revision=revision,
                lot_number=lot_number,
            )

        return cls(
            serial=uut_serial or "UUT001",
            part_number=part_number,
            revision=revision,
            lot_number=lot_number,
        )


class EnvironmentUUTProvider:
    """Resolves UUT identity from environment variables.

    Variable naming convention:
    - ``TESTERKIT_UUT_SERIAL`` — single serial for all sites
    - ``TESTERKIT_UUT_SERIAL_SITE_<N>`` — per-site serial by index
    - ``TESTERKIT_UUT_PART_NUMBER`` — shared part number
    - ``TESTERKIT_UUT_REVISION`` — shared revision
    - ``TESTERKIT_UUT_LOT_NUMBER`` — shared lot number
    """

    def get_uut(self, site_index: int) -> UUT:
        """Return UUT from environment variables."""
        # Try site-specific first, then global
        env_key = f"TESTERKIT_UUT_SERIAL_SITE_{site_index}"
        serial = os.environ.get(env_key) or os.environ.get("TESTERKIT_UUT_SERIAL")

        if not serial:
            raise UUTProviderError(
                f"No UUT serial in environment for site {site_index}. "
                f"Set {env_key} or TESTERKIT_UUT_SERIAL."
            )

        return UUT(
            serial=serial,
            part_number=os.environ.get("TESTERKIT_UUT_PART_NUMBER"),
            revision=os.environ.get("TESTERKIT_UUT_REVISION"),
            lot_number=os.environ.get("TESTERKIT_UUT_LOT_NUMBER"),
        )

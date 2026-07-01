"""Fixture site resolution for multi-UUT testing.

Resolves fixture sites against a station config to validate that all
referenced instrument roles exist. Single-UUT fixtures (using
``connections`` instead of ``sites``) are normalized to a single
implicit site at index 0.
"""

from __future__ import annotations

from collections import Counter

from pydantic import BaseModel, Field

from litmus.models.test_config import FixtureConfig, FixtureConnection


class ResolvedSite(BaseModel):
    """A fixture site with validated instrument references.

    Attributes:
        site_index: 0-based position of this site in the fixture's sites list.
        site_name: Optional human label (e.g. "left"), frozen at run time.
        connections: FixtureConnection mappings for this site's UUT.
        instrument_roles: Set of station instrument roles this site needs.
        uut_resource: Per-site UUT connection string (e.g., COM3, /dev/ttyUSB0).
    """

    site_index: int
    site_name: str | None = None
    connections: dict[str, FixtureConnection] = Field(default_factory=dict)
    instrument_roles: set[str] = Field(default_factory=set)
    uut_resource: str | None = None


def resolve_fixture_sites(
    fixture_config: FixtureConfig,
    station_instruments: set[str] | None = None,
) -> list[ResolvedSite]:
    """Resolve fixture sites and validate instrument references.

    For single-UUT fixtures (``connections``), returns one site at index 0.
    For multi-UUT fixtures (``sites``), returns one site per entry.

    Args:
        fixture_config: Fixture configuration with connections or sites.
        station_instruments: Set of instrument role names from station config.
            If provided, validates that all fixture connection instrument
            references exist in the station.

    Returns:
        List of ResolvedSite ordered by site_index.

    Raises:
        ValueError: If a fixture connection references an instrument
            role not present in the station config.
    """
    if fixture_config.sites:
        sites = [
            _build_resolved_site(
                site_index,
                site.name,
                site.connections,
                uut_resource=site.uut_resource,
            )
            for site_index, site in enumerate(fixture_config.sites)
        ]
    else:
        sites = [
            _build_resolved_site(
                0,
                None,
                fixture_config.connections,
                uut_resource=fixture_config.uut_resource,
            )
        ]

    if station_instruments is not None:
        _validate_instrument_refs(sites, station_instruments, fixture_config.id)

    return sites


def _build_resolved_site(
    site_index: int,
    site_name: str | None,
    connections: dict[str, FixtureConnection],
    *,
    uut_resource: str | None = None,
) -> ResolvedSite:
    """Build a ResolvedSite from fixture connections."""
    roles = {conn.instrument for conn in connections.values()}
    for conn in connections.values():
        if conn.route is not None:
            roles.add(conn.route.switch)
    return ResolvedSite(
        site_index=site_index,
        site_name=site_name,
        connections=connections,
        instrument_roles=roles,
        uut_resource=uut_resource,
    )


def format_known_sites(sites: list[ResolvedSite]) -> str:
    """Format a fixture's site list for "known sites" error messages.

    Bare (unnamed) sites render as a bracketed index (``[1]``); named
    sites render by name. Shared by every caller that needs to tell an
    operator which ``--site`` / ``--uut-serials`` tokens are valid.
    """
    return ", ".join(s.site_name or f"[{s.site_index}]" for s in sites)


def resolve_site_token(token: str, sites: list[ResolvedSite]) -> int:
    """Resolve a ``--site`` / ``--uut-serials`` key token to a concrete site_index.

    ``token`` is tried as a decimal ``site_index`` first; if that
    fails, it's matched against ``sites`` by name. The single resolver
    behind both the CLI ``--site`` flag (``hooks._resolve_and_install_site``)
    and ``--uut-serials`` keyed parsing (``CLIUUTProvider.parse_serials``).

    Integer tokens are validated against ``sites`` only when ``sites``
    is non-empty — bringup / single-site runs (no multi-site fixture
    loaded) accept any numeric ``--site`` value permissively, matching
    the pre-existing single-process behavior. Name tokens always
    require a match; an empty ``sites`` list means "no such name."

    Raises:
        ValueError: ``token`` doesn't resolve to a known site.
    """
    try:
        site_index = int(token)
    except ValueError:
        matched = [s for s in sites if s.site_name == token]
        if not matched:
            raise ValueError(
                f"{token!r} not in fixture's site list (known: {format_known_sites(sites)})."
            ) from None
        return matched[0].site_index

    if sites and site_index not in {s.site_index for s in sites}:
        raise ValueError(
            f"{token!r} not in fixture's site list (known: {format_known_sites(sites)})."
        )
    return site_index


def detect_shared_instruments(sites: list[ResolvedSite]) -> set[str]:
    """Detect instrument roles shared by multiple sites.

    An instrument role is "shared" when two or more sites reference it.

    Args:
        sites: Resolved fixture sites.

    Returns:
        Set of instrument role names that appear in 2+ sites.
    """
    counts: Counter[str] = Counter()
    for site in sites:
        counts.update(site.instrument_roles)
    return {role for role, count in counts.items() if count >= 2}


def _validate_instrument_refs(
    sites: list[ResolvedSite],
    station_instruments: set[str],
    fixture_id: str,
) -> None:
    """Validate that all fixture connection instrument refs exist in station."""
    for site in sites:
        missing = site.instrument_roles - station_instruments
        if missing:
            raise ValueError(
                f"Fixture '{fixture_id}' site {site.site_index} references instruments "
                f"not in station config: {', '.join(sorted(missing))}"
            )

"""Capability matching service for products and stations."""

from litmus.matching.service import (
    CapabilityMatch,
    CapabilityRequirement,
    MatchResult,
    PartialStationMatch,
    StationCapability,
    StationMatch,
    capability_satisfies,
    check_station_compatibility,
    find_all_station_matches,
    find_compatible_stations,
    find_partial_stations,
    get_required_capabilities,
    get_station_capabilities,
    list_products_summary,
    match_capabilities,
)

__all__ = [
    "CapabilityMatch",
    "CapabilityRequirement",
    "MatchResult",
    "PartialStationMatch",
    "StationCapability",
    "StationMatch",
    "capability_satisfies",
    "check_station_compatibility",
    "find_all_station_matches",
    "find_compatible_stations",
    "find_partial_stations",
    "get_required_capabilities",
    "get_station_capabilities",
    "list_products_summary",
    "match_capabilities",
]

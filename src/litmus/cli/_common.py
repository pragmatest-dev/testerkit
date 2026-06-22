"""Shared cross-module CLI helpers."""

from __future__ import annotations

import click


def _format_instrument(resource: str, info: object | None) -> str:
    """Format a discovered instrument for display.

    Returns a one-line string like 'Keysight 34465A (TCPIP::...)' or
    'TCPIP::... (could not identify)'.
    """
    if info and getattr(info, "manufacturer", None) and getattr(info, "model", None):
        mfr = info.manufacturer  # type: ignore[union-attr]
        model = info.model  # type: ignore[union-attr]
        serial = getattr(info, "serial", None) or ""
        serial_str = f" (SN: {serial})" if serial else ""
        return f"{mfr} {model}{serial_str} ({resource})"
    return f"{resource} (could not identify)"


def _discover_instruments(interactive: bool = True) -> dict[str, dict[str, dict[str, str]]] | None:
    """Discover instruments and build station data.

    Args:
        interactive: If True, prompt user for role names.
            If False, auto-name from catalog type.

    Returns:
        Station dict with ``instruments`` mapping, or None if nothing found.
    """
    from litmus.instruments.discovery import discover_and_identify

    click.echo("\nDiscovering instruments...")
    results = discover_and_identify(["visa"])

    from litmus.instruments.discovery import InstrumentInfo

    # Flatten all discovered instruments
    all_instruments: list[tuple[str, InstrumentInfo | None]] = []
    for _proto, items in results.items():
        all_instruments.extend(items)

    if not all_instruments:
        click.echo("  No instruments found.")
        return None

    # First pass: determine default role for each instrument
    pending: list[tuple[str, InstrumentInfo | None, str]] = []
    for resource, info in all_instruments:
        click.echo(f"  {_format_instrument(resource, info)}")

        # Determine default role from catalog type
        role = None
        if info and info.manufacturer and info.model:
            try:
                from litmus.store import find_by_model

                entry = find_by_model(info.manufacturer, info.model)
                if entry and entry.type:
                    role = entry.type
            except (ImportError, OSError, ValueError):
                pass

        if not role and info and info.model:
            role = info.model.lower().replace("-", "_").replace(" ", "_")

        if not role:
            role = "instrument"

        pending.append((resource, info, role))

    # Second pass: prompt for roles (interactive) or auto-assign
    assigned: list[tuple[str, str, InstrumentInfo | None]] = []
    for resource, info, default_role in pending:
        if interactive:
            label = info.model if info and info.model else resource
            role = click.prompt(f"    {label} role", default=default_role)
            if role.lower() == "skip":
                continue
        else:
            role = default_role
        assigned.append((role, resource, info))

    if not assigned:
        return None

    # Deduplicate roles: if multiple instruments share a role, number them
    role_counts: dict[str, int] = {}
    for role, _r, _i in assigned:
        role_counts[role] = role_counts.get(role, 0) + 1

    role_index: dict[str, int] = {}
    station_instruments: dict[str, dict] = {}
    for role, resource, info in assigned:
        if role_counts[role] > 1:
            idx = role_index.get(role, 0) + 1
            role_index[role] = idx
            final_role = f"{role}{idx}"
        else:
            final_role = role

        station_instruments[final_role] = {"resource": resource}

    return {"instruments": station_instruments}


def _get_data_dir(data_dir):
    """Resolve results directory from option or project config."""
    from litmus.data.data_dir import resolve_data_dir

    return str(resolve_data_dir(data_dir))

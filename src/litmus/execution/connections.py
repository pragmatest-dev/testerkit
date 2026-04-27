"""Resolve ``litmus_specs`` / ``litmus_connections`` markers to fixture connections.

The two markers compose into a single ordered list of
:class:`FixtureConnection` instances that the test body iterates via
``ctx.connections``. ``litmus_specs`` supplies the characteristic
context (its ``resolved_pins`` set); ``litmus_connections`` supplies
an explicit named-connection list or an instrument-channel selector.
When both are present the connection list narrows the spec's pin set;
when only one is present the resolver derives the iteration set from
it directly.

Pulled out of ``plugin.py`` so that file stays focused on pytest hook
registration. The pytest fixture wrapping this resolver
(``_litmus_resolve_connections``) lives in ``plugin.py``.
"""

from __future__ import annotations

from collections.abc import KeysView, ValuesView
from contextvars import Token
from typing import TYPE_CHECKING, Any

from litmus.execution._state import push_active_connection, reset_active_connection
from litmus.models.test_config import FixtureConfig, FixtureConnection

if TYPE_CHECKING:
    import pytest


class ConnectionResolutionError(Exception):
    """Raised when ``litmus_specs`` / ``litmus_connections`` cannot resolve.

    Runner-neutral: the pytest adapter catches this and re-raises as
    ``ConnectionResolutionError`` at the fixture boundary; other runners adapt
    to their own user-error type.
    """


class ConnectionIterator:
    """Iterable + name-addressable view over the bound :class:`FixtureConnection` list.

    Built by ``_litmus_resolve_connections`` from a test's spec/connections markers.
    The instance is iterable (drives the ``_active_connection_var``
    ContextVar so drivers can route to the *current* connection) and
    also supports ``__getitem__`` / ``keys()`` / ``values()`` /
    ``__contains__`` for passive name lookup. Iteration is single-use
    and follows insertion order; lookup never pushes the ContextVar.
    """

    def __init__(self, connections: list[FixtureConnection]) -> None:
        self._connections = connections
        self._by_name: dict[str, FixtureConnection] = {c.name: c for c in connections}
        self._idx = 0
        self._token: Token[FixtureConnection | None] | None = None
        self.started = False

    def __iter__(self) -> ConnectionIterator:
        return self

    def __next__(self) -> FixtureConnection:
        if self._token is not None:
            reset_active_connection(self._token)
            self._token = None
        if self._idx >= len(self._connections):
            raise StopIteration
        connection = self._connections[self._idx]
        self._idx += 1
        self.started = True
        self._token = push_active_connection(connection)
        return connection

    def __len__(self) -> int:
        return len(self._connections)

    def __getitem__(self, key: str | int) -> FixtureConnection:
        if isinstance(key, int):
            return self._connections[key]
        return self._by_name[key]

    def __contains__(self, key: object) -> bool:
        return key in self._by_name

    def keys(self) -> KeysView[str]:
        return self._by_name.keys()

    def values(self) -> ValuesView[FixtureConnection]:
        return self._by_name.values()

    def cleanup(self) -> None:
        """Pop any lingering active-connection token on teardown or mid-iter exit."""
        if self._token is not None:
            reset_active_connection(self._token)
            self._token = None


def _spec_pin_set(characteristic: str, spec_ctx: Any) -> set[str]:
    """Return the characteristic's ``resolved_pins`` as a set, validating context."""
    if spec_ctx is None:
        raise ConnectionResolutionError(
            f"litmus_specs(characteristic={characteristic!r}) "
            "requires a product spec (load via --spec or products/ auto-discovery)."
        )
    char = spec_ctx.product.characteristics.get(characteristic)
    if char is None:
        raise ConnectionResolutionError(
            f"Characteristic {characteristic!r} not found in product {spec_ctx.product.id!r}."
        )
    return set(char.resolved_pins)


def _spec_connections_from_fixture(
    pin_set: set[str],
    spec_ctx: Any,
    fixture_cfg: FixtureConfig,
) -> list[FixtureConnection]:
    """Match a characteristic's pin set against the fixture (by ``dut_pin`` or ``net``)."""
    matched: list[FixtureConnection] = []
    for pin_id in pin_set:
        pin = spec_ctx.product.pins.get(pin_id)
        net = pin.net if pin else None
        for conn in fixture_cfg.connections.values():
            if conn.dut_pin == pin_id or (net is not None and conn.net == net):
                matched.append(conn)
                break
    return matched


def _validate_connections_kwargs(
    kwargs: dict[str, Any],
) -> tuple[list[str] | None, dict[str, Any] | None]:
    """Validate the ``litmus_connections`` payload and return its branch.

    Exactly one of ``connections`` (named lookup) / ``instrument_channels``
    (raw ``inst:channel`` selector) must be set.
    """
    connections = kwargs.get("connections")
    instrument_channels = kwargs.get("instrument_channels")
    if connections is not None and instrument_channels is not None:
        raise ConnectionResolutionError(
            "litmus_connections must set exactly one of connections or instrument_channels."
        )
    if connections is None and instrument_channels is None:
        raise ConnectionResolutionError(
            "litmus_connections requires either connections=[...] or instrument_channels={...}."
        )
    return connections, instrument_channels


def _named_connections(
    names: list[str],
    fixture_cfg: FixtureConfig | None,
    char_pins: set[str] | None,
) -> list[FixtureConnection]:
    """Resolve ``connections=[name, ...]`` against the fixture, in user-listed order."""
    if fixture_cfg is None:
        raise ConnectionResolutionError(
            "litmus_connections(connections=...) requires a fixture config; "
            "connection names are only meaningful relative to a fixture YAML."
        )
    resolved: list[FixtureConnection] = []
    for name in names:
        conn = fixture_cfg.connections.get(name)
        if conn is None:
            raise ConnectionResolutionError(
                f"Fixture connection {name!r} not found in fixture config."
            )
        resolved.append(conn)
    if char_pins is not None:
        invalid = [conn.name for conn in resolved if conn.dut_pin not in char_pins]
        if invalid:
            raise ConnectionResolutionError(
                f"litmus_connections names {invalid} resolve to pins outside "
                f"characteristic's pin set {sorted(char_pins)}."
            )
    return resolved


def _channel_selectors(
    instrument_channels: dict[str, Any],
    fixture_cfg: FixtureConfig | None,
    char_pins: set[str] | None,
) -> list[FixtureConnection]:
    """Resolve ``instrument_channels={inst: [chs]}`` in user-listed order.

    With a fixture, matches each (instrument, channel) selector against
    fixture connections; with ``char_pins`` set, every match must be in
    that pin set. Without a fixture, synthesizes minimal
    :class:`FixtureConnection` stubs from the (instrument, channel)
    tuples — usable for early bringup where the DUT-pin mapping isn't
    yet captured.
    """
    if fixture_cfg is None:
        stubs: list[FixtureConnection] = []
        for inst_name, channels in instrument_channels.items():
            if channels == "all":
                raise ConnectionResolutionError(
                    f"litmus_connections instrument_channels[{inst_name!r}]='all' "
                    "requires a fixture config."
                )
            for ch in channels:
                ch_str = str(ch)
                stubs.append(
                    FixtureConnection(
                        name=f"{inst_name}_ch{ch_str}",
                        instrument=inst_name,
                        instrument_channel=ch_str,
                    )
                )
        return stubs

    matched: list[FixtureConnection] = []
    for inst_name, channels in instrument_channels.items():
        if channels == "all":
            inst_matches = [
                conn for conn in fixture_cfg.connections.values() if conn.instrument == inst_name
            ]
        else:
            inst_matches = []
            for ch in channels:
                ch_str = str(ch)
                hit = next(
                    (
                        conn
                        for conn in fixture_cfg.connections.values()
                        if conn.instrument == inst_name and conn.instrument_channel == ch_str
                    ),
                    None,
                )
                if hit is None:
                    raise ConnectionResolutionError(
                        f"litmus_connections instrument_channels[{inst_name!r}]={ch_str!r} "
                        "matched no fixture connection."
                    )
                inst_matches.append(hit)
        matched.extend(inst_matches)

    if char_pins is not None:
        invalid = [
            f"{conn.instrument}/{conn.instrument_channel}"
            for conn in matched
            if conn.dut_pin not in char_pins
        ]
        if invalid:
            raise ConnectionResolutionError(
                f"litmus_connections selected channels {invalid} resolve to pins outside "
                f"characteristic's pin set {sorted(char_pins)}."
            )
    return matched


def resolve_test_connections(
    characteristic: str | None,
    conn_marker: pytest.Mark | None,
    spec_ctx: Any,
    fixture_cfg: FixtureConfig | None,
) -> list[FixtureConnection]:
    """Resolve the iterable connection set from ``litmus_specs`` / ``litmus_connections``.

    The two markers compose:

    * **spec only** — derive connections from the characteristic's pin
      set, matched against the fixture (or empty if no fixture loaded).
    * **connections only** — explicit lookup against the fixture, or
      synthesized stubs when ``instrument_channels`` is given without
      a fixture.
    * **spec + connections** — connections narrows the spec's pin set.
      Iteration order follows the user-listed connections; every
      selected connection must lie within the characteristic's pin set
      (or, for fixtureless ``instrument_channels``, no DUT-pin
      validation is possible and the stubs pass through).
    """
    if characteristic is None and conn_marker is None:
        return []

    char_pins: set[str] | None = None
    if characteristic is not None:
        char_pins = _spec_pin_set(characteristic, spec_ctx)

    if conn_marker is None:
        assert char_pins is not None
        if fixture_cfg is None:
            return []
        return _spec_connections_from_fixture(char_pins, spec_ctx, fixture_cfg)

    names, instrument_channels = _validate_connections_kwargs(dict(conn_marker.kwargs))
    if names is not None:
        return _named_connections(names, fixture_cfg, char_pins)
    assert instrument_channels is not None
    return _channel_selectors(instrument_channels, fixture_cfg, char_pins)

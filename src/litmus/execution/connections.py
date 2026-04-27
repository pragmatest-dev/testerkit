"""Resolve ``litmus_characteristics`` / ``litmus_connections`` markers to fixture connections.

The two markers compose into a single ordered list of
:class:`FixtureConnection` instances that the test body iterates via
``ctx.connections``. ``litmus_characteristics`` supplies the
characteristic list (each char's ``resolved_pins``);
``litmus_connections`` supplies an explicit named-connection list or
an instrument-channel selector. When both are present the connection
list narrows the union of chars' pin sets; when only one is present
the resolver derives the iteration set from it directly.

Pulled out of ``plugin.py`` so that file stays focused on pytest hook
registration. The pytest fixture wrapping this resolver
(``_litmus_resolve_connections``) lives in ``plugin.py``.
"""

from __future__ import annotations

from collections.abc import KeysView, Sequence, ValuesView
from contextvars import Token
from typing import TYPE_CHECKING, Any

from litmus.execution._state import (
    push_active_characteristic,
    push_active_connection,
    reset_active_characteristic,
    reset_active_connection,
)
from litmus.models.test_config import FixtureConfig, FixtureConnection

if TYPE_CHECKING:
    import pytest


class ConnectionResolutionError(Exception):
    """Raised when ``litmus_characteristics`` / ``litmus_connections`` cannot resolve.

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

    def __init__(
        self,
        connections: list[FixtureConnection],
        conn_to_char: dict[str, str] | None = None,
        _parent: ConnectionIterator | None = None,
    ) -> None:
        self._connections = connections
        self._by_name: dict[str, FixtureConnection] = {c.name: c for c in connections}
        # Per-connection char lookup, keyed by connection name. Built
        # by ``resolve_test_connections`` from the spec; empty when no
        # chars are in scope (pure ``litmus_connections`` bringup).
        self._conn_to_char: dict[str, str] = conn_to_char or {}
        # Parent iterator (for ``for_characteristic`` sub-iterators) so
        # iterating the child also marks the parent ``started`` —
        # actual iteration, not iterator construction, is what the
        # consumption guard cares about.
        self._parent: ConnectionIterator | None = _parent
        self._idx = 0
        self._conn_token: Token[FixtureConnection | None] | None = None
        self._char_token: Token[str | None] | None = None
        self.started = False

    def __iter__(self) -> ConnectionIterator:
        return self

    def __next__(self) -> FixtureConnection:
        self._reset_tokens()
        if self._idx >= len(self._connections):
            raise StopIteration
        connection = self._connections[self._idx]
        self._idx += 1
        self.started = True
        if self._parent is not None:
            self._parent.started = True
        self._conn_token = push_active_connection(connection)
        char = self._conn_to_char.get(connection.name)
        if char is not None:
            self._char_token = push_active_characteristic(char)
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

    def for_characteristic(self, char_id: str) -> ConnectionIterator:
        """Return a fresh iterator scoped to connections owned by ``char_id``.

        Iterates only the subset whose ``conn_to_char`` mapping matches
        ``char_id``. Each ``__next__`` pushes the active-char ContextVar
        to ``char_id`` (so ``verify`` stamps the right
        ``characteristic_id``) AND marks the parent iterator
        ``started`` — so the consumption guard in
        ``_litmus_resolve_connections`` sees real iteration, not just
        iterator construction. Building a sub-iterator and never
        iterating it does NOT count as consumption.
        """
        subset = [
            conn for conn in self._connections if self._conn_to_char.get(conn.name) == char_id
        ]
        return ConnectionIterator(subset, {c.name: char_id for c in subset}, _parent=self)

    def _reset_tokens(self) -> None:
        if self._char_token is not None:
            reset_active_characteristic(self._char_token)
            self._char_token = None
        if self._conn_token is not None:
            reset_active_connection(self._conn_token)
            self._conn_token = None

    def cleanup(self) -> None:
        """Pop any lingering active-connection / active-char tokens on teardown."""
        self._reset_tokens()


def _per_char_pins(characteristics: Sequence[str], spec_ctx: Any) -> dict[str, list[str]]:
    """Return per-char ``resolved_pins`` lists, validating each char.

    Keys preserve user-listed order; values are each char's pin list.
    Raises if the spec context is missing or a char is unknown.
    """
    if spec_ctx is None:
        raise ConnectionResolutionError(
            f"litmus_characteristics({list(characteristics)!r}) "
            "requires a product spec (load via --spec or products/ auto-discovery)."
        )
    out: dict[str, list[str]] = {}
    for char_id in characteristics:
        char = spec_ctx.product.characteristics.get(char_id)
        if char is None:
            raise ConnectionResolutionError(
                f"Characteristic {char_id!r} not found in product {spec_ctx.product.id!r}."
            )
        out[char_id] = list(char.resolved_pins)
    return out


def _pick_connection_for_pin(
    pin_id: str,
    target_function: Any,
    pin_net: str | None,
    fixture_cfg: FixtureConfig,
) -> FixtureConnection | None:
    """Pick the fixture connection that routes ``(pin, function)``.

    Match priority, highest first:

    1. **Exact**: char's ``function`` and conn's ``function`` are both set
       and equal — the strongest match.
    2. **Pin-only**: at least one side has no ``function``. Either the
       char doesn't declare what function it needs (backward-compatible
       with pre-function fixtures) or the conn is generic
       (function-unset). The first such conn for the pin wins.

    Connections where both sides set ``function`` but the values differ
    are skipped — a wired-up DC connection isn't valid for an AC char.
    Match by ``dut_pin`` first; falls back to net match when
    ``dut_pin`` is unset on the connection.
    """
    exact_match: FixtureConnection | None = None
    pin_only_match: FixtureConnection | None = None
    for conn in fixture_cfg.connections.values():
        pin_hit = conn.dut_pin == pin_id or (pin_net is not None and conn.net == pin_net)
        if not pin_hit:
            continue
        if target_function is not None and conn.function == target_function:
            exact_match = exact_match or conn
        elif target_function is None or conn.function is None:
            # At least one side is None — pin-only fallback.
            pin_only_match = pin_only_match or conn
        # else: both sides set but different — skip (e.g. char wants
        # ac_voltage but this conn is dc_voltage-only).
    return exact_match or pin_only_match


def _resolve_chars_to_connections(
    characteristics: Sequence[str],
    spec_ctx: Any,
    fixture_cfg: FixtureConfig,
) -> tuple[list[FixtureConnection], dict[str, str]]:
    """Match each char's ``(pin, function)`` against the fixture.

    Returns a deduplicated connection list (preserving char order, then
    char-pin order) plus a ``conn_name → char_id`` lookup. When the
    same fixture connection is selected by multiple chars, the
    first-listing char owns it (matching marker order).

    Edge case: when two chars share a pin but neither declares
    ``function:`` (or both share the same function), they bind to the
    same connection — only the first-listed char owns it in
    ``conn_to_char``. ``ctx.connections.for_characteristic(<later_char>)``
    will return an empty subset for the loser. To avoid this, declare
    distinct ``function:`` values on the chars and the matching
    connections (the per-function tie-break in
    :func:`_pick_connection_for_pin`), or put the chars on different
    pins.
    """
    matched: list[FixtureConnection] = []
    conn_to_char: dict[str, str] = {}
    seen_names: set[str] = set()
    for char_id in characteristics:
        char = spec_ctx.product.characteristics.get(char_id)
        if char is None:
            continue
        for pin_id in char.resolved_pins:
            pin = spec_ctx.product.pins.get(pin_id)
            net = pin.net if pin else None
            conn = _pick_connection_for_pin(pin_id, char.function, net, fixture_cfg)
            if conn is None or conn.name in seen_names:
                continue
            matched.append(conn)
            conn_to_char[conn.name] = char_id
            seen_names.add(conn.name)
    return matched, conn_to_char


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
                f"the union of declared characteristics' pin sets {sorted(char_pins)}."
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
                f"the union of declared characteristics' pin sets {sorted(char_pins)}."
            )
    return matched


def resolve_test_connections(
    characteristics: Sequence[str],
    conn_marker: pytest.Mark | None,
    spec_ctx: Any,
    fixture_cfg: FixtureConfig | None,
) -> tuple[list[FixtureConnection], dict[str, str]]:
    """Resolve the iterable connection set + per-connection char lookup.

    The two markers compose:

    * **chars only** — derive connections from each char's
      ``(pin, function)``, matched against the fixture. Empty list
      when ``characteristics`` is empty AND no ``conn_marker``.
    * **connections only** — explicit lookup against the fixture, or
      synthesized stubs when ``instrument_channels`` is given without
      a fixture.
    * **chars + connections** — connections narrows the union of the
      chars' pin sets. Iteration order follows the user-listed
      connections; every selected connection must lie within the
      union pin set (or, for fixtureless ``instrument_channels``, no
      DUT-pin validation is possible and the stubs pass through).

    Returns a tuple of ``(connections, conn_to_char)`` where
    ``conn_to_char`` maps each connection's ``name`` to the char that
    owns it (used by ``ConnectionIterator`` to push the active-char
    ContextVar per connection). The lookup is empty for the
    pure-``litmus_connections`` bringup case.
    """
    if not characteristics and conn_marker is None:
        return [], {}

    per_char_pins: dict[str, list[str]] = {}
    if characteristics:
        per_char_pins = _per_char_pins(characteristics, spec_ctx)

    if conn_marker is None:
        if fixture_cfg is None:
            # Chars in scope but no fixture loaded → ctx.connections is
            # an empty iterator. Tests that only resolve limits (and
            # never iterate connections) work fine in this mode; tests
            # that DO iterate trip the post-run "declared but didn't
            # consume" guard in _litmus_resolve_connections.
            return [], {}
        return _resolve_chars_to_connections(characteristics, spec_ctx, fixture_cfg)

    union_pins: set[str] | None = None
    if per_char_pins:
        union_pins = {pin for pins in per_char_pins.values() for pin in pins}

    names, instrument_channels = _validate_connections_kwargs(dict(conn_marker.kwargs))
    if names is not None:
        connections = _named_connections(names, fixture_cfg, union_pins)
    else:
        assert instrument_channels is not None
        # Tighten bug #15: chars in scope + fixtureless instrument_channels
        # would silently bypass the per-char pin-set cross-check (no
        # dut_pin to validate against). That makes litmus_characteristics
        # dead weight in this branch — flag it loud.
        if characteristics and fixture_cfg is None:
            raise ConnectionResolutionError(
                f"litmus_characteristics({list(characteristics)!r}) cannot "
                "validate fixtureless instrument_channels selectors — there "
                "is no dut_pin to cross-check against. Drop "
                "litmus_characteristics for pure bringup, or load a "
                "fixture."
            )
        connections = _channel_selectors(instrument_channels, fixture_cfg, union_pins)

    conn_to_char = _connections_to_char_map(connections, per_char_pins, spec_ctx)
    return connections, conn_to_char


def _connections_to_char_map(
    connections: list[FixtureConnection],
    per_char_pins: dict[str, list[str]],
    spec_ctx: Any,
) -> dict[str, str]:
    """Build the ``conn.name → char_id`` lookup for explicit ``litmus_connections`` paths.

    For each connection, pick the first char (in user-declared order)
    whose pin set covers the connection's ``dut_pin``. Connections
    outside any char's pin set (only possible for fixtureless
    ``instrument_channels`` stubs without a spec) get no entry.
    """
    if not per_char_pins:
        return {}
    out: dict[str, str] = {}
    for conn in connections:
        pin_id = conn.dut_pin
        if pin_id is None and spec_ctx is not None and conn.net is not None:
            net_pins = spec_ctx.product.get_pins_by_net(conn.net)
            pin_id = net_pins[0] if net_pins else None
        if pin_id is None:
            continue
        for char_id, pins in per_char_pins.items():
            if pin_id in pins:
                out[conn.name] = char_id
                break
    return out

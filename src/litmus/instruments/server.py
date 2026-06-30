"""Instrument RPC server for persistent shared instruments.

Hosts connected driver instances and exposes them to worker processes
via TCP localhost. Workers get ``RemoteInstrumentProxy`` objects that
look exactly like local drivers — tests never know the difference.

The server runs as a daemon thread in the orchestrator/owner process.
Workers connect via ``multiprocessing.connection``. Ref-counted shutdown
ensures instruments stay alive until all consumers disconnect.
"""

from __future__ import annotations

import logging
import threading
import time
from multiprocessing.connection import Client, Listener
from typing import Any

from litmus.instruments.locks import ResourceInUse

logger = logging.getLogger(__name__)

_GETATTR = "__getattr__"
_SETATTR = "__setattr__"
_DIR = "__dir__"
_DISCONNECT = "__disconnect__"
_RESERVE = "__reserve__"
_RELEASE = "__release__"

_DEAD_CLIENT_TIMEOUT = 15.0


class InstrumentServer:
    """TCP-based RPC server for shared instrument access.

    Holds connected driver instances keyed by role. Each client connection
    is handled in its own thread. Locking is per-resource (driver session),
    not per-role — roles that share a resource share a lock. Roles on
    different resources run concurrently. Switches with
    ``concurrent=True`` skip the lock entirely.

    Lease arbitration is per-resource, refcounted, and re-entrant per
    connection. A client sends ``_RESERVE`` to acquire an exclusive step-
    duration lease; ``_RELEASE`` decrements the refcount. Normal RPC
    operations are refused when a *different* connection holds the lease.
    When no lease is held the per-resource ``threading.Lock`` serialises
    all callers as before, preserving existing behaviour unchanged.

    Args:
        instruments: Connected driver instances keyed by role.
        resources: Map of role → resource string (e.g., VISA address).
            Roles sharing a resource string share a lock. Roles without
            an entry get a unique lock (equivalent to per-role locking).
        concurrent_roles: Roles that allow concurrent access (e.g., switches).
            These skip both the per-resource lock and the lease table.
    """

    def __init__(
        self,
        instruments: dict[str, Any],
        resources: dict[str, str] | None = None,
        concurrent_roles: set[str] | None = None,
    ) -> None:
        self._instruments = instruments
        self._concurrent_roles = concurrent_roles or set()

        resource_map = resources or {}
        concurrent = concurrent_roles or set()
        self._role_to_resource: dict[str, str] = {}
        for role in instruments:
            if role in concurrent:
                continue
            self._role_to_resource[role] = resource_map.get(role, role)

        unique_resources = set(self._role_to_resource.values())
        self._locks: dict[str, threading.Lock] = {
            resource: threading.Lock() for resource in unique_resources
        }
        self._meta_lock = threading.Lock()
        self._leases: dict[str, tuple[int, int]] = {}
        self._lease_condition = threading.Condition(threading.Lock())
        self._listener: Listener | None = None
        self._serve_thread: threading.Thread | None = None
        self._stopping = threading.Event()
        self._ref_count = 0
        self._ref_lock = threading.Lock()
        self._address: tuple[str, int] | None = None

    @property
    def address(self) -> tuple[str, int] | None:
        """Server address as ``(host, port)``."""
        return self._address

    @property
    def address_str(self) -> str:
        """Server address as ``host:port`` string."""
        if self._address is None:
            raise RuntimeError("Server not started")
        return f"{self._address[0]}:{self._address[1]}"

    def start(self) -> None:
        """Start listening for connections."""
        self._listener = Listener(("127.0.0.1", 0), family="AF_INET")
        addr = self._listener.address
        assert isinstance(addr, tuple)
        self._address = addr
        self._stopping.clear()

        with self._ref_lock:
            self._ref_count = 1

        self._serve_thread = threading.Thread(
            target=self._accept_loop,
            name="litmus-instrument-server",
            daemon=True,
        )
        self._serve_thread.start()
        logger.info("InstrumentServer started on %s", self.address_str)

    def stop(self, *, force: bool = False) -> None:
        """Unregister the owner. Server shuts down when ref_count hits 0.

        Args:
            force: If True, shut down immediately regardless of ref_count.
        """
        if force:
            self._shutdown()
            return

        with self._ref_lock:
            self._ref_count = max(0, self._ref_count - 1)
            if self._ref_count > 0:
                logger.info(
                    "Owner unregistered, %d clients remain",
                    self._ref_count,
                )
                return

        self._shutdown()

    def _shutdown(self) -> None:
        """Force-close the listener and stop accepting."""
        self._stopping.set()
        if self._listener is not None:
            try:
                self._listener.close()
            except OSError:
                pass
            self._listener = None
        logger.info("InstrumentServer stopped")

    def _accept_loop(self) -> None:
        """Accept client connections until stopped."""
        while not self._stopping.is_set():
            try:
                if self._listener is None:
                    break
                conn = self._listener.accept()
            except OSError:
                break

            with self._ref_lock:
                self._ref_count += 1

            t = threading.Thread(
                target=self._handle_client,
                args=(conn,),
                name="litmus-instrument-client",
                daemon=True,
            )
            t.start()

    def _acquire_lease(self, resource_key: str, conn_id: int, timeout: float) -> bool:
        """Acquire or increment the refcounted lease for resource_key.

        Re-entrant for the same conn_id: increments without blocking.
        Blocks up to ``timeout`` seconds (``-1`` = forever) waiting for a
        different holder to release. Returns ``False`` immediately when
        ``timeout >= 0`` and the deadline has passed.
        """
        deadline = None if timeout < 0 else time.monotonic() + timeout
        with self._lease_condition:
            while True:
                existing = self._leases.get(resource_key)
                if existing is None:
                    self._leases[resource_key] = (1, conn_id)
                    return True
                refcount, holder = existing
                if holder == conn_id:
                    self._leases[resource_key] = (refcount + 1, conn_id)
                    return True
                if deadline is not None:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        return False
                    self._lease_condition.wait(remaining)
                else:
                    self._lease_condition.wait()

    def _release_lease(self, resource_key: str, conn_id: int) -> None:
        """Decrement the lease refcount; notify waiters when it reaches zero."""
        with self._lease_condition:
            existing = self._leases.get(resource_key)
            if existing is None:
                return
            refcount, holder = existing
            if holder != conn_id:
                return
            if refcount > 1:
                self._leases[resource_key] = (refcount - 1, conn_id)
            else:
                del self._leases[resource_key]
                self._lease_condition.notify_all()

    def _release_all_leases(self, conn_id: int) -> None:
        """Release every lease held by conn_id. Called on disconnect."""
        with self._lease_condition:
            released_keys = [k for k, (_, holder) in self._leases.items() if holder == conn_id]
            for key in released_keys:
                del self._leases[key]
            if released_keys:
                self._lease_condition.notify_all()

    def _handle_client(self, conn: Any) -> None:
        """Handle requests from a single client connection."""
        conn_id = id(conn)
        try:
            while not self._stopping.is_set():
                try:
                    msg = conn.recv()
                except (EOFError, OSError):
                    break

                if msg[0] == _DISCONNECT:
                    break

                if msg[0] == _RESERVE:
                    _, role, timeout = msg
                    resource_key = self._role_to_resource.get(role)
                    if resource_key is None:
                        try:
                            conn.send(("ok", None))
                        except (OSError, EOFError):
                            break
                        continue
                    granted = self._acquire_lease(resource_key, conn_id, timeout)
                    try:
                        if granted:
                            conn.send(("ok", None))
                        else:
                            conn.send(("resource_in_use", resource_key))
                    except (OSError, EOFError):
                        if granted:
                            self._release_lease(resource_key, conn_id)
                        break
                    continue

                if msg[0] == _RELEASE:
                    _, role = msg
                    resource_key = self._role_to_resource.get(role)
                    if resource_key is not None:
                        self._release_lease(resource_key, conn_id)
                    try:
                        conn.send(("ok", None))
                    except (OSError, EOFError):
                        break
                    continue

                role, action, *rest = msg
                driver = self._instruments.get(role)
                if driver is None:
                    conn.send(("error", f"Unknown role: {role!r}"))
                    continue

                resource_key = self._role_to_resource.get(role)

                if resource_key:
                    with self._lease_condition:
                        existing = self._leases.get(resource_key)
                        refused = existing is not None and existing[1] != conn_id
                    if refused:
                        try:
                            conn.send(
                                (
                                    "error",
                                    f"ResourceInUse: {resource_key!r} reserved by another client",
                                )
                            )
                        except (OSError, EOFError):
                            break
                        continue

                lock = self._locks.get(resource_key) if resource_key else None
                try:
                    if lock is not None:
                        if not lock.acquire(timeout=_DEAD_CLIENT_TIMEOUT):
                            logger.warning(
                                "Lock for '%s' timed out after %.0fs — "
                                "possible dead client, force-proceeding",
                                role,
                                _DEAD_CLIENT_TIMEOUT,
                            )
                            if resource_key:
                                with self._meta_lock:
                                    if self._locks.get(resource_key) is lock:
                                        replacement = threading.Lock()
                                        self._locks[resource_key] = replacement
                                        lock = replacement
                                    else:
                                        lock = self._locks[resource_key]
                            lock.acquire()
                    try:
                        result = self._dispatch(driver, action, rest)
                        conn.send(("ok", result))
                    finally:
                        if lock is not None:
                            lock.release()
                except Exception as exc:
                    try:
                        conn.send(("error", f"{type(exc).__name__}: {exc}"))
                    except (OSError, EOFError):
                        break
        finally:
            self._release_all_leases(conn_id)
            try:
                conn.close()
            except OSError:
                pass

            with self._ref_lock:
                self._ref_count = max(0, self._ref_count - 1)
                remaining = self._ref_count

            if remaining == 0:
                self._shutdown()

    @staticmethod
    def _dispatch(driver: Any, action: str, rest: list[Any]) -> Any:
        """Execute a single request against the driver."""
        if action == _GETATTR:
            name = rest[0]
            attr = getattr(driver, name)
            if callable(attr):
                return ("callable", name)
            return ("value", attr)

        if action == _SETATTR:
            name, value = rest[0], rest[1]
            setattr(driver, name, value)
            return None

        if action == _DIR:
            return dir(driver)

        if action.startswith("_"):
            raise ValueError(f"Cannot invoke private method via RPC: {action!r}")
        args = rest[0] if rest else ()
        kwargs = rest[1] if len(rest) > 1 else {}
        attr = getattr(driver, action)
        return attr(*args, **kwargs)


class RemoteInstrumentProxy:
    """Transparent proxy to an instrument hosted by an InstrumentServer.

    Looks exactly like a local driver. Method calls, property reads, and
    property writes are forwarded via RPC. Tests, RoutedProxy, and
    InstrumentProxy all interact with this transparently.

    Args:
        address: Server address as ``(host, port)`` tuple.
        role: Instrument role name on the server.
    """

    def __init__(self, address: tuple[str, int], role: str) -> None:
        object.__setattr__(self, "_address", address)
        object.__setattr__(self, "_role", role)
        object.__setattr__(self, "_conn", Client(address, family="AF_INET"))

    def __getattr__(self, name: str) -> Any:
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")

        conn.send((role, _GETATTR, name))
        status, result = conn.recv()
        if status == "error":
            raise RuntimeError(result)

        tag, payload = result
        if tag == "callable":

            def _remote_call(*args: Any, **kwargs: Any) -> Any:
                conn.send((role, name, args, kwargs))
                st, res = conn.recv()
                if st == "error":
                    raise RuntimeError(res)
                return res

            return _remote_call

        return payload

    def __setattr__(self, name: str, value: Any) -> None:
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")
        conn.send((role, _SETATTR, name, value))
        status, result = conn.recv()
        if status == "error":
            raise RuntimeError(result)

    def __dir__(self) -> list[str]:
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")
        conn.send((role, _DIR))
        status, result = conn.recv()
        if status == "error":
            return []
        return result

    def __repr__(self) -> str:
        role = object.__getattribute__(self, "_role")
        address = object.__getattribute__(self, "_address")
        return f"<RemoteInstrumentProxy({role!r}, {address[0]}:{address[1]})>"

    def reserve(self, timeout: float = 0) -> None:
        """Acquire an exclusive step-duration lease on the server.

        Re-entrant for this proxy's connection: a second call increments
        the refcount without blocking. Each call requires a matching
        :meth:`release_reservation`.

        Args:
            timeout: Seconds to wait for a live holder.  ``0`` = fail
                immediately; positive = bounded wait; ``-1`` = wait forever.

        Raises:
            ResourceInUse: If a different connection holds the lease and
                ``timeout`` expires before it is released.
            RuntimeError: On unexpected server errors.
        """
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")
        conn.send((_RESERVE, role, timeout))
        status, result = conn.recv()
        if status == "resource_in_use":
            raise ResourceInUse(result)
        if status == "error":
            raise RuntimeError(result)

    def release_reservation(self) -> None:
        """Decrement the lease refcount on the server.

        A no-op (server-side) if no lease is held by this connection.
        """
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")
        conn.send((_RELEASE, role))
        _status, _result = conn.recv()

    def disconnect(self) -> None:
        """Public lifecycle method: ask remote driver to disconnect, then
        release this proxy's server connection.

        Used by :func:`litmus.instruments.lifecycle.disconnect`. The
        remote-side call is best-effort (the driver may not implement
        ``disconnect``); this proxy's own connection is always released
        so the multiplex server can reclaim its slot.
        """
        conn = object.__getattribute__(self, "_conn")
        role = object.__getattribute__(self, "_role")
        try:
            conn.send((role, "disconnect", (), {}))
            conn.recv()
        except (OSError, EOFError, RuntimeError):
            pass
        self._disconnect()

    def _disconnect(self) -> None:
        """Release the proxy's server connection (no remote call)."""
        conn = object.__getattribute__(self, "_conn")
        try:
            conn.send((_DISCONNECT,))
        except (OSError, EOFError):
            pass
        try:
            conn.close()
        except OSError:
            pass

    def __del__(self) -> None:
        try:
            self._disconnect()
        except (OSError, EOFError):
            pass


def connect_to_server(address_str: str) -> tuple[str, int]:
    """Parse a ``host:port`` address string into a tuple.

    Args:
        address_str: Address in ``host:port`` format.

    Returns:
        ``(host, port)`` tuple for use with ``RemoteInstrumentProxy``.
    """
    host, port_str = address_str.rsplit(":", 1)
    return (host, int(port_str))

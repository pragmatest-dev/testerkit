"""Tests for InstrumentServer and RemoteInstrumentProxy."""

import os
import threading
import time

import pytest

from litmus.instruments.locks import ResourceInUse
from litmus.instruments.server import (
    InstrumentServer,
    RemoteInstrumentProxy,
    connect_to_server,
)


def _addr(server: InstrumentServer) -> tuple[str, int]:
    """Return server.address after asserting start() has populated it."""
    assert server.address is not None, "server not started"
    return server.address


class FakeDriver:
    """Minimal instrument driver for testing."""

    def __init__(self):
        self.voltage = 3.3
        self.output_enabled = False
        self._calls: list[str] = []

    def measure_voltage(self) -> float:
        self._calls.append("measure_voltage")
        return self.voltage

    def set_voltage(self, v: float) -> None:
        self._calls.append(f"set_voltage({v})")
        self.voltage = v

    def query(self, cmd: str) -> str:
        self._calls.append(f"query({cmd})")
        return f"response:{cmd}"

    def disconnect(self) -> None:
        """Implement the lifecycle hook so InstrumentPool can clean up cleanly."""
        self._calls.append("disconnect")


class FakeSwitch:
    """Fake switch driver for concurrent access testing."""

    def __init__(self):
        self.closed: list[list[str]] = []

    def close_channels(self, channels: list[str]) -> None:
        self.closed.append(channels)

    def open_channels(self, channels: list[str]) -> None:
        pass

    def open_all(self) -> None:
        pass


@pytest.fixture
def server_and_driver():
    """Create a server with a single FakeDriver and yield (server, driver)."""
    driver = FakeDriver()
    server = InstrumentServer({"dmm": driver})
    server.start()
    yield server, driver
    server.stop(force=True)


class TestInstrumentServer:
    """Server lifecycle and basic operations."""

    def test_start_assigns_address(self):
        server = InstrumentServer({"dmm": FakeDriver()})
        server.start()
        try:
            assert server.address is not None
            host, port = server.address
            assert host == "127.0.0.1"
            assert port > 0
        finally:
            server.stop(force=True)

    def test_address_str_format(self):
        server = InstrumentServer({"dmm": FakeDriver()})
        server.start()
        try:
            assert "127.0.0.1:" in server.address_str
        finally:
            server.stop(force=True)

    def test_address_str_before_start_raises(self):
        server = InstrumentServer({"dmm": FakeDriver()})
        with pytest.raises(RuntimeError, match="not started"):
            _ = server.address_str

    def test_stop_force(self):
        server = InstrumentServer({"dmm": FakeDriver()})
        server.start()
        server.stop(force=True)
        # After force stop, listener is closed
        assert server._listener is None


class TestRemoteInstrumentProxy:
    """Proxy transparency — tests never know it's remote."""

    def test_method_call(self, server_and_driver):
        server, driver = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "dmm")
        try:
            result = proxy.measure_voltage()
            assert result == pytest.approx(3.3)
        finally:
            proxy._disconnect()

    def test_method_with_args(self, server_and_driver):
        server, driver = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "dmm")
        try:
            proxy.set_voltage(5.0)
            assert driver.voltage == pytest.approx(5.0)
        finally:
            proxy._disconnect()

    def test_property_write(self, server_and_driver):
        server, driver = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "dmm")
        try:
            proxy.output_enabled = True
            assert driver.output_enabled is True
        finally:
            proxy._disconnect()

    def test_repr(self, server_and_driver):
        server, driver = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "dmm")
        try:
            r = repr(proxy)
            assert "RemoteInstrumentProxy" in r
            assert "dmm" in r
        finally:
            proxy._disconnect()

    def test_dir_returns_driver_attributes(self, server_and_driver):
        server, driver = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "dmm")
        try:
            d = dir(proxy)
            assert "measure_voltage" in d
            assert "set_voltage" in d
        finally:
            proxy._disconnect()

    def test_unknown_role_returns_error(self, server_and_driver):
        server, _ = server_and_driver
        proxy = RemoteInstrumentProxy(_addr(server), "nonexistent")
        try:
            with pytest.raises(RuntimeError, match="Unknown role"):
                proxy.measure_voltage()
        finally:
            proxy._disconnect()


class TestServerLocking:
    """Per-resource locking serializes access to shared driver sessions."""

    def test_serialized_access(self):
        """Two proxies accessing same role are serialized."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy1 = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy2 = RemoteInstrumentProxy(_addr(server), "dmm")

            results = []

            def measure(proxy, name):
                v = proxy.measure_voltage()
                results.append((name, v))

            t1 = threading.Thread(target=measure, args=(proxy1, "p1"))
            t2 = threading.Thread(target=measure, args=(proxy2, "p2"))
            t1.start()
            t2.start()
            t1.join()
            t2.join()

            assert len(results) == 2
            assert all(v == pytest.approx(3.3) for _, v in results)

            proxy1._disconnect()
            proxy2._disconnect()
        finally:
            server.stop(force=True)

    def test_shared_resource_shares_lock(self):
        """Two roles on same resource share a lock (same driver session)."""
        dmm = FakeDriver()
        scope = FakeDriver()
        server = InstrumentServer(
            {"dmm": dmm, "scope": scope},
            resources={"dmm": "TCPIP::10.0.0.1::INSTR", "scope": "TCPIP::10.0.0.1::INSTR"},
        )
        # Both map to same resource → same lock object
        dmm_resource = server._role_to_resource["dmm"]
        scope_resource = server._role_to_resource["scope"]
        assert dmm_resource == scope_resource
        assert len(server._locks) == 1

    def test_different_resources_different_locks(self):
        """Two roles on different resources get independent locks."""
        dmm = FakeDriver()
        psu = FakeDriver()
        server = InstrumentServer(
            {"dmm": dmm, "psu": psu},
            resources={"dmm": "TCPIP::10.0.0.1::INSTR", "psu": "TCPIP::10.0.0.2::INSTR"},
        )
        dmm_resource = server._role_to_resource["dmm"]
        psu_resource = server._role_to_resource["psu"]
        assert dmm_resource != psu_resource
        assert len(server._locks) == 2

    def test_no_resource_falls_back_to_role(self):
        """Roles without resource strings get unique per-role locks."""
        dmm = FakeDriver()
        psu = FakeDriver()
        server = InstrumentServer({"dmm": dmm, "psu": psu})
        # No resources passed → each role is its own lock key
        assert server._role_to_resource["dmm"] == "dmm"
        assert server._role_to_resource["psu"] == "psu"
        assert len(server._locks) == 2

    def test_concurrent_roles_skip_lock(self):
        """Concurrent roles (switches) allow simultaneous access."""
        switch = FakeSwitch()
        server = InstrumentServer(
            {"matrix": switch},
            concurrent_roles={"matrix"},
        )
        server.start()
        try:
            proxy1 = RemoteInstrumentProxy(_addr(server), "matrix")
            proxy2 = RemoteInstrumentProxy(_addr(server), "matrix")

            barrier = threading.Barrier(2, timeout=5)
            results = []

            def close_channels(proxy, channels, name):
                barrier.wait()  # Ensure both start at the same time
                proxy.close_channels(channels)
                results.append(name)

            t1 = threading.Thread(
                target=close_channels,
                args=(proxy1, ["r0c0"], "p1"),
            )
            t2 = threading.Thread(
                target=close_channels,
                args=(proxy2, ["r1c0"], "p2"),
            )
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert len(results) == 2

            proxy1._disconnect()
            proxy2._disconnect()
        finally:
            server.stop(force=True)


class TestRefCounting:
    """Server shuts down when all clients disconnect."""

    def test_server_stays_alive_with_clients(self):
        """Server stays alive when owner stops but clients remain."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()

        proxy = RemoteInstrumentProxy(_addr(server), "dmm")

        # Ensure the proxy connection is fully accepted by the server
        # before the owner stops (otherwise ref_count race)
        v = proxy.measure_voltage()
        assert v == pytest.approx(3.3)

        # Owner stops (non-force) — server stays alive
        server.stop()

        # Proxy still works
        v = proxy.measure_voltage()
        assert v == pytest.approx(3.3)

        # Now disconnect the proxy
        proxy._disconnect()

        # Give the server a moment to process disconnect
        time.sleep(0.1)

        # Server should have shut down (ref_count == 0)
        assert server._stopping.is_set()

    def test_force_stop_ignores_clients(self):
        """Force stop kills server immediately."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()

        # Force stop without client disconnect
        server.stop(force=True)
        assert server._stopping.is_set()


class TestPoolIntegration:
    """InstrumentPool returns RemoteInstrumentProxy when env vars are set."""

    def test_acquire_returns_remote_proxy(self):
        """Pool.acquire() returns RemoteInstrumentProxy for shared roles."""
        from uuid import uuid4

        from litmus.instruments.pool import InstrumentPool
        from litmus.models.instrument import InstrumentRecord

        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            # Set env vars as SiteRunner would
            os.environ["_LITMUS_INSTRUMENT_SERVER"] = server.address_str
            os.environ["_LITMUS_SHARED_ROLES"] = "dmm"

            pool = InstrumentPool(
                session_id=uuid4(),
                event_log=None,
                channel_store=None,
                mock_all=True,
            )
            record = InstrumentRecord(
                role="dmm",
                instrument_id="dmm",
                driver="examples.drivers.DMM",
                resource="",
                protocol="visa",
                mocked=True,
            )

            inst = pool.acquire("dmm", record)

            # Should be a RemoteInstrumentProxy, not a local mock
            assert isinstance(inst, RemoteInstrumentProxy)

            # Should work transparently
            v = inst.measure_voltage()
            assert v == pytest.approx(3.3)

            pool.disconnect_all()
        finally:
            os.environ.pop("_LITMUS_INSTRUMENT_SERVER", None)
            os.environ.pop("_LITMUS_SHARED_ROLES", None)
            server.stop(force=True)

    def test_acquire_local_without_env_vars(self):
        """Pool.acquire() falls back to local connection without env vars."""
        from uuid import uuid4

        from litmus.instruments.pool import InstrumentPool
        from litmus.models.instrument import InstrumentRecord

        # Ensure env vars are NOT set
        os.environ.pop("_LITMUS_INSTRUMENT_SERVER", None)
        os.environ.pop("_LITMUS_SHARED_ROLES", None)

        pool = InstrumentPool(
            session_id=uuid4(),
            event_log=None,
            channel_store=None,
            mock_all=True,
        )
        record = InstrumentRecord(
            role="dmm",
            instrument_id="dmm",
            driver="examples.drivers.DMM",
            resource="",
            protocol="visa",
            mocked=True,
        )

        inst = pool.acquire("dmm", record)

        # Should NOT be a RemoteInstrumentProxy
        assert not isinstance(inst, RemoteInstrumentProxy)

        pool.disconnect_all()


class TestSubprocessSerialization:
    """Two subprocess workers serialize through the server."""

    def test_two_workers_get_correct_results(self):
        """Spawn two subprocesses that both call the server concurrently."""
        import subprocess
        import sys

        driver = FakeDriver()
        driver.voltage = 5.0
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            # Worker script: connect, measure, print result
            worker_script = f"""
import sys
from litmus.instruments.server import RemoteInstrumentProxy, connect_to_server
addr = connect_to_server("{server.address_str}")
proxy = RemoteInstrumentProxy(addr, "dmm")
v = proxy.measure_voltage()
print(f"{{v}}")
proxy._disconnect()
"""
            # Spawn two workers concurrently
            p1 = subprocess.Popen(
                [sys.executable, "-c", worker_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p2 = subprocess.Popen(
                [sys.executable, "-c", worker_script],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            out1, err1 = p1.communicate(timeout=10)
            out2, err2 = p2.communicate(timeout=10)

            assert p1.returncode == 0, f"Worker 1 failed: {err1.decode()}"
            assert p2.returncode == 0, f"Worker 2 failed: {err2.decode()}"

            v1 = float(out1.decode().strip())
            v2 = float(out2.decode().strip())

            assert v1 == pytest.approx(5.0)
            assert v2 == pytest.approx(5.0)

            # Server should have recorded 4 calls total (2 workers × getattr + call)
            assert len(driver._calls) == 2
        finally:
            server.stop(force=True)

    def test_two_workers_mutations_visible(self):
        """Worker 1 sets voltage, worker 2 reads the updated value."""
        import subprocess
        import sys

        driver = FakeDriver()
        driver.voltage = 1.0
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            # Worker 1: set voltage to 9.9
            set_script = f"""
from litmus.instruments.server import RemoteInstrumentProxy, connect_to_server
addr = connect_to_server("{server.address_str}")
proxy = RemoteInstrumentProxy(addr, "dmm")
proxy.set_voltage(9.9)
proxy._disconnect()
"""
            # Worker 2: read voltage (runs after worker 1)
            read_script = f"""
from litmus.instruments.server import RemoteInstrumentProxy, connect_to_server
addr = connect_to_server("{server.address_str}")
proxy = RemoteInstrumentProxy(addr, "dmm")
v = proxy.measure_voltage()
print(f"{{v}}")
proxy._disconnect()
"""
            # Run sequentially to prove state is shared
            p1 = subprocess.run(
                [sys.executable, "-c", set_script],
                capture_output=True,
                timeout=10,
            )
            assert p1.returncode == 0, f"Set failed: {p1.stderr.decode()}"

            p2 = subprocess.run(
                [sys.executable, "-c", read_script],
                capture_output=True,
                timeout=10,
            )
            assert p2.returncode == 0, f"Read failed: {p2.stderr.decode()}"

            v = float(p2.stdout.decode().strip())
            assert v == pytest.approx(9.9)
        finally:
            server.stop(force=True)


class TestConnectToServer:
    """Address string parsing."""

    def test_parse_address(self):
        host, port = connect_to_server("127.0.0.1:12345")
        assert host == "127.0.0.1"
        assert port == 12345

    def test_parse_localhost(self):
        host, port = connect_to_server("localhost:8080")
        assert host == "localhost"
        assert port == 8080


class TestServerLeases:
    """Step-duration refcounted per-connection lease table.

    Tests the _RESERVE / _RELEASE verbs on InstrumentServer and the
    RemoteInstrumentProxy.reserve / release_reservation surface.
    """

    def test_reserve_timeout_zero_refused_when_held(self):
        """B RESERVE with timeout=0 raises ResourceInUse while A holds the lease."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)
                with pytest.raises(ResourceInUse):
                    proxy_b.reserve(timeout=0)
            finally:
                proxy_a.release_reservation()
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_reserve_waits_for_live_holder(self):
        """B RESERVE with positive timeout blocks until A releases, then succeeds."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)

                result: list[bool] = []
                error: list[Exception] = []

                def b_reserve():
                    try:
                        proxy_b.reserve(timeout=2.0)
                        result.append(True)
                    except Exception as exc:
                        error.append(exc)

                t = threading.Thread(target=b_reserve, daemon=True)
                t.start()

                time.sleep(0.05)
                proxy_a.release_reservation()

                t.join(timeout=3.0)
                assert not t.is_alive()
                assert error == []
                assert result == [True]

                proxy_b.release_reservation()
            finally:
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_operation_refused_while_different_client_holds_lease(self):
        """B RPC on a leased resource raises RuntimeError(ResourceInUse) while A holds."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)
                with pytest.raises(RuntimeError, match="ResourceInUse"):
                    proxy_b.measure_voltage()
            finally:
                proxy_a.release_reservation()
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_operation_allowed_after_release(self):
        """B's RPC succeeds once A releases the lease."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)
                proxy_a.release_reservation()
                result = proxy_b.measure_voltage()
                assert result == pytest.approx(3.3)
            finally:
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_reentrant_reserve_needs_matching_releases(self):
        """Two RESERVEs from the same connection require two RELEASEs to free the lease."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)
                proxy_a.reserve(timeout=0)

                proxy_a.release_reservation()
                with pytest.raises(ResourceInUse):
                    proxy_b.reserve(timeout=0)

                proxy_a.release_reservation()
                proxy_b.reserve(timeout=0)
                proxy_b.release_reservation()
            finally:
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_unleased_ops_behave_as_before(self):
        """Without any reservation, both clients call the driver freely."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                results = []
                errors: list[Exception] = []

                def measure(proxy, name):
                    try:
                        results.append((name, proxy.measure_voltage()))
                    except Exception as exc:
                        errors.append(exc)

                t1 = threading.Thread(target=measure, args=(proxy_a, "a"))
                t2 = threading.Thread(target=measure, args=(proxy_b, "b"))
                t1.start()
                t2.start()
                t1.join(timeout=5)
                t2.join(timeout=5)

                assert errors == []
                assert len(results) == 2
                assert all(v == pytest.approx(3.3) for _, v in results)
            finally:
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_disconnect_releases_all_leases(self):
        """A client that disconnects without releasing frees its leases."""
        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")
            try:
                proxy_a.reserve(timeout=0)
                proxy_a.reserve(timeout=0)

                proxy_a._disconnect()

                proxy_b.reserve(timeout=1.0)
                proxy_b.release_reservation()
            finally:
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_concurrent_roles_skip_leasing(self):
        """Concurrent roles are not subject to lease arbitration."""
        switch = FakeSwitch()
        server = InstrumentServer(
            {"matrix": switch},
            concurrent_roles={"matrix"},
        )
        server.start()
        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "matrix")
            proxy_b = RemoteInstrumentProxy(_addr(server), "matrix")
            try:
                proxy_a.reserve(timeout=0)
                proxy_a.reserve(timeout=0)
                proxy_b.reserve(timeout=0)
                proxy_b.close_channels(["r0c0"])
                proxy_a.release_reservation()
                proxy_a.release_reservation()
                proxy_b.release_reservation()
            finally:
                proxy_a._disconnect()
                proxy_b._disconnect()
        finally:
            server.stop(force=True)

    def test_meta_lock_guards_dead_client_rebind(self, monkeypatch):
        """Concurrent dead-client lock reclaims end up on one shared replacement lock."""
        import litmus.instruments.server as srv_mod

        monkeypatch.setattr(srv_mod, "_DEAD_CLIENT_TIMEOUT", 0.05)

        driver = FakeDriver()
        server = InstrumentServer({"dmm": driver})
        server.start()

        original_lock = server._locks["dmm"]
        original_lock.acquire()

        try:
            proxy_a = RemoteInstrumentProxy(_addr(server), "dmm")
            proxy_b = RemoteInstrumentProxy(_addr(server), "dmm")

            results: list[float] = []
            errors: list[Exception] = []

            def do_rpc(proxy):
                try:
                    results.append(proxy.measure_voltage())
                except Exception as exc:
                    errors.append(exc)

            t1 = threading.Thread(target=do_rpc, args=(proxy_a,), daemon=True)
            t2 = threading.Thread(target=do_rpc, args=(proxy_b,), daemon=True)
            t1.start()
            t2.start()
            t1.join(timeout=5)
            t2.join(timeout=5)

            assert errors == [], f"unexpected errors: {errors}"
            assert len(results) == 2
            assert all(v == pytest.approx(3.3) for v in results)
            assert server._locks["dmm"] is not original_lock

            proxy_a._disconnect()
            proxy_b._disconnect()
        finally:
            try:
                original_lock.release()
            except RuntimeError:
                pass
            server.stop(force=True)

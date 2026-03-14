"""Tests for RoutedProxy transparent instrument wrapping."""

from unittest.mock import MagicMock

from litmus.instruments.routed_proxy import RoutedProxy


class TestRoutedProxy:
    def _make_proxy(self):
        instrument = MagicMock()
        instrument.measure_voltage.return_value = 3.3
        instrument.resource = "GPIB::16::INSTR"
        route_manager = MagicMock()
        proxy = RoutedProxy(instrument, "vout_measure", route_manager)
        return proxy, instrument, route_manager

    def test_first_access_activates_route(self):
        proxy, instrument, rm = self._make_proxy()
        result = proxy.measure_voltage()

        rm.activate.assert_called_once_with("vout_measure")
        instrument.measure_voltage.assert_called_once()
        assert result == 3.3

    def test_second_access_does_not_reactivate(self):
        proxy, instrument, rm = self._make_proxy()
        proxy.measure_voltage()
        proxy.measure_voltage()

        # activate called only once
        rm.activate.assert_called_once_with("vout_measure")
        assert instrument.measure_voltage.call_count == 2

    def test_different_methods_share_activation(self):
        proxy, instrument, rm = self._make_proxy()
        instrument.measure_current.return_value = 0.001

        proxy.measure_voltage()
        proxy.measure_current()

        rm.activate.assert_called_once_with("vout_measure")
        instrument.measure_voltage.assert_called_once()
        instrument.measure_current.assert_called_once()

    def test_repr_pending(self):
        proxy, instrument, _ = self._make_proxy()
        r = repr(proxy)
        assert "pending" in r
        assert "vout_measure" in r

    def test_repr_active(self):
        proxy, _, _ = self._make_proxy()
        proxy.measure_voltage()
        r = repr(proxy)
        assert "active" in r

    def test_attribute_passthrough(self):
        proxy, instrument, _ = self._make_proxy()
        instrument.some_property = 42
        assert proxy.some_property == 42


class TestRoutedProxyWithResolver:
    """RoutedProxy with resolver callable for shared instruments."""

    def _make_proxy_with_resolver(self):
        instrument = MagicMock()
        instrument.measure_voltage.return_value = 5.5
        route_manager = MagicMock()
        resolver = MagicMock(return_value=instrument)
        proxy = RoutedProxy(None, "vout_measure", route_manager, resolver=resolver)
        return proxy, instrument, route_manager, resolver

    def test_resolver_called_on_access(self):
        proxy, instrument, rm, resolver = self._make_proxy_with_resolver()
        result = proxy.measure_voltage()

        rm.activate.assert_called_once_with("vout_measure")
        resolver.assert_called()
        instrument.measure_voltage.assert_called_once()
        assert result == 5.5

    def test_resolver_not_called_without_access(self):
        _, _, _, resolver = self._make_proxy_with_resolver()
        resolver.assert_not_called()

    def test_resolver_used_instead_of_instrument(self):
        """When resolver is set, _instrument is ignored."""
        other_instrument = MagicMock()
        other_instrument.measure_voltage.return_value = 9.9
        route_manager = MagicMock()
        resolver = MagicMock(return_value=other_instrument)

        proxy = RoutedProxy(
            MagicMock(),  # This should be ignored
            "vout_measure",
            route_manager,
            resolver=resolver,
        )
        result = proxy.measure_voltage()
        assert result == 9.9
        other_instrument.measure_voltage.assert_called_once()

    def test_repr_with_resolver(self):
        proxy, _, _, _ = self._make_proxy_with_resolver()
        r = repr(proxy)
        assert "pending" in r
        assert "vout_measure" in r

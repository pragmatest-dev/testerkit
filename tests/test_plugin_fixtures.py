"""Tests for instrument fixture auto-registration and InstrumentAccessor."""

import textwrap

import pytest

from litmus.models.instrument import InstrumentRecord
from litmus.pytest_plugin import InstrumentAccessor

pytest_plugins = ["pytester"]


# =============================================================================
# InstrumentAccessor unit tests
# =============================================================================


class TestInstrumentAccessor:
    """Tests for the InstrumentAccessor class."""

    def _make_accessor(self, instruments=None, records=None):
        instruments = instruments or {}
        records = records or {}
        return InstrumentAccessor(instruments, records)

    def test_call_returns_instrument(self):
        dmm_obj = object()
        accessor = self._make_accessor(instruments={"dmm": dmm_obj})
        assert accessor("dmm") is dmm_obj

    def test_call_missing_role_raises_keyerror(self):
        accessor = self._make_accessor(instruments={"dmm": object(), "psu": object()})
        with pytest.raises(KeyError, match="No instrument with role 'eload'") as exc_info:
            accessor("eload")
        assert "dmm" in str(exc_info.value)
        assert "psu" in str(exc_info.value)

    def test_call_missing_role_empty_instruments(self):
        accessor = self._make_accessor()
        with pytest.raises(KeyError, match="Available: \\(none\\)"):
            accessor("dmm")

    def test_by_type_filters_by_driver_path(self):
        dmm1 = object()
        dmm2 = object()
        psu = object()
        instruments = {"dmm1": dmm1, "dmm2": dmm2, "psu": psu}
        records = {
            "dmm1": InstrumentRecord(
                role="dmm1",
                instrument_id="k2000_1",
                resource="FAKE",
                driver="drivers.Keithley2000",
            ),
            "dmm2": InstrumentRecord(
                role="dmm2",
                instrument_id="k2000_2",
                resource="FAKE",
                driver="drivers.Keithley2000",
            ),
            "psu": InstrumentRecord(
                role="psu",
                instrument_id="e3631a",
                resource="FAKE",
                driver="drivers.E3631A",
            ),
        }
        accessor = self._make_accessor(instruments, records)
        result = accessor.by_type("drivers.Keithley2000")
        assert result == {"dmm1": dmm1, "dmm2": dmm2}

    def test_by_type_no_matches(self):
        accessor = self._make_accessor(
            instruments={"dmm": object()},
            records={
                "dmm": InstrumentRecord(
                    role="dmm",
                    instrument_id="k2000",
                    resource="FAKE",
                    driver="drivers.Keithley2000",
                ),
            },
        )
        result = accessor.by_type("drivers.NonExistent")
        assert result == {}

    def test_by_type_skips_roles_not_in_instruments(self):
        """Records may exist for roles whose driver failed to load."""
        dmm = object()
        accessor = self._make_accessor(
            instruments={"dmm": dmm},
            records={
                "dmm": InstrumentRecord(
                    role="dmm",
                    instrument_id="k2000",
                    resource="FAKE",
                    driver="drivers.Keithley2000",
                ),
                "psu": InstrumentRecord(
                    role="psu",
                    instrument_id="e3631a",
                    resource="FAKE",
                    driver="drivers.Keithley2000",
                ),
            },
        )
        result = accessor.by_type("drivers.Keithley2000")
        assert result == {"dmm": dmm}

    def test_roles_returns_sorted_list(self):
        accessor = self._make_accessor(
            instruments={"psu": object(), "dmm": object(), "eload": object()}
        )
        assert accessor.roles() == ["dmm", "eload", "psu"]

    def test_roles_empty(self):
        accessor = self._make_accessor()
        assert accessor.roles() == []


# =============================================================================
# Integration tests using pytester
# =============================================================================


class TestAutoRegistration:
    """Tests for auto-registration of instrument role fixtures."""

    @pytest.fixture(autouse=True)
    def _asyncio_config(self, pytester):
        pytester.makeini("[pytest]\nasyncio_default_fixture_loop_scope = function")

    def test_auto_registered_fixtures_available(self, pytester):
        """Station config roles become available as pytest fixtures."""
        # Create station config
        pytester.mkdir("stations")
        pytester.makefile(
            ".yaml",
            **{
                "stations/station": textwrap.dedent("""\
                    id: station
                    name: Test Station
                    instruments:
                      dmm:
                        type: dmm
                        driver: builtins.object
                        resource: "FAKE::ADDR"
                      psu:
                        type: psu
                        driver: builtins.object
                        resource: "FAKE::ADDR2"
                """),
            },
        )

        # Neutralize logger to avoid duckdb import errors in child process
        pytester.makeconftest(
            textwrap.dedent("""\
            import pytest

            @pytest.fixture(scope="session", autouse=True)
            def logger():
                yield None
        """)
        )

        # Create a test that uses the auto-registered fixtures
        pytester.makepyfile(
            test_auto=textwrap.dedent("""\
                def test_dmm_fixture(dmm):
                    '''dmm fixture should be auto-registered from station config.'''
                    # With mock instruments, dmm will be a Mock object
                    assert dmm is not None

                def test_psu_fixture(psu):
                    '''psu fixture should be auto-registered from station config.'''
                    assert psu is not None
            """),
        )

        result = pytester.runpytest("--mock-instruments", "--station=station", "-v")
        result.assert_outcomes(passed=2)

    def test_auto_register_no_station(self, pytester):
        """No station config → no error, no auto-registered fixtures."""
        pytester.makeconftest(
            textwrap.dedent("""\
            import pytest

            @pytest.fixture(scope="session", autouse=True)
            def logger():
                yield None
        """)
        )

        pytester.makepyfile(
            test_no_station=textwrap.dedent("""\
                def test_basic():
                    assert True
            """),
        )

        result = pytester.runpytest("-v", "--station=nonexistent")
        result.assert_outcomes(passed=1)

    def test_conftest_override(self, pytester):
        """User fixture in conftest takes precedence over auto-registered."""
        pytester.mkdir("stations")
        pytester.makefile(
            ".yaml",
            **{
                "stations/station": textwrap.dedent("""\
                    id: station
                    name: Test Station
                    instruments:
                      psu:
                        type: psu
                        driver: builtins.object
                        resource: "FAKE::ADDR"
                """),
            },
        )

        pytester.makeconftest(
            textwrap.dedent("""\
                import pytest

                @pytest.fixture(scope="session", autouse=True)
                def logger():
                    yield None

                @pytest.fixture(scope="session")
                def psu():
                    '''Override the auto-registered psu fixture.'''
                    return "custom_psu"
            """),
        )

        pytester.makepyfile(
            test_override=textwrap.dedent("""\
                def test_psu_is_overridden(psu):
                    assert psu == "custom_psu"
            """),
        )

        result = pytester.runpytest("--mock-instruments", "-v")
        result.assert_outcomes(passed=1)

    def test_instrument_accessor_fixture(self, pytester):
        """The instrument() accessor fixture works for role lookup."""
        pytester.mkdir("stations")
        pytester.makefile(
            ".yaml",
            **{
                "stations/station": textwrap.dedent("""\
                    id: station
                    name: Test Station
                    instruments:
                      dmm:
                        type: dmm
                        driver: builtins.object
                        resource: "FAKE::ADDR"
                """),
            },
        )

        pytester.makeconftest(
            textwrap.dedent("""\
            import pytest

            @pytest.fixture(scope="session", autouse=True)
            def logger():
                yield None
        """)
        )

        pytester.makepyfile(
            test_accessor=textwrap.dedent("""\
                import pytest

                def test_accessor_call(instrument):
                    '''instrument("dmm") should return the instrument.'''
                    dmm = instrument("dmm")
                    assert dmm is not None

                def test_accessor_roles(instrument):
                    '''instrument.roles() should list available roles.'''
                    roles = instrument.roles()
                    assert "dmm" in roles

                def test_accessor_missing(instrument):
                    '''instrument("nonexistent") should raise KeyError.'''
                    with pytest.raises(KeyError, match="nonexistent"):
                        instrument("nonexistent")
            """),
        )

        result = pytester.runpytest("--mock-instruments", "--station=station", "-v")
        result.assert_outcomes(passed=3)

    def test_empty_instruments_section(self, pytester):
        """Station config with empty instruments → no error."""
        pytester.mkdir("stations")
        pytester.makefile(
            ".yaml",
            **{
                "stations/station": textwrap.dedent("""\
                    id: station
                    name: Test Station
                    instruments: {}
                """),
            },
        )

        pytester.makeconftest(
            textwrap.dedent("""\
            import pytest

            @pytest.fixture(scope="session", autouse=True)
            def logger():
                yield None
        """)
        )

        pytester.makepyfile(
            test_empty=textwrap.dedent("""\
                def test_basic():
                    assert True
            """),
        )

        result = pytester.runpytest("-v")
        result.assert_outcomes(passed=1)

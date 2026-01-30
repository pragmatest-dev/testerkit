"""Generated tests for TPS54302 DC-DC Converter.

Auto-generated from product specification: demo/specs/tps54302.yaml
Based on datasheet: SLVSD12A (March 2024)

Test coverage:
- Output voltage accuracy at various loads
- Load regulation
- Efficiency at nominal conditions
- Output ripple at full load
- Quiescent current

Instruments required:
- PSU (voltage source for VIN)
- DMM (voltage/current measurement)
- Scope (ripple measurement)
- Eload (load current setting)
"""

from decimal import Decimal

import pytest

from litmus.config.models import Limit
from litmus.data.models import Outcome
from litmus.execution.harness import TestHarness
from litmus.products import SpecContext


# Load spec once for the module
SPEC_PATH = "demo/specs/tps54302.yaml"


@pytest.fixture(scope="module")
def spec():
    """Load TPS54302 spec with 10% guardband for production testing."""
    return SpecContext.from_file(SPEC_PATH, guardband_pct=Decimal("10"))


# =============================================================================
# Simulated Instrument Fixtures
# =============================================================================


@pytest.fixture
def psu():
    """Simulated power supply."""
    class SimPSU:
        def __init__(self):
            self.voltage = 5.0
            self.current_limit = 2.0
            self.enabled = False

        def set_voltage(self, v):
            self.voltage = v

        def set_current_limit(self, i):
            self.current_limit = i

        def enable(self):
            self.enabled = True

        def disable(self):
            self.enabled = False

    psu = SimPSU()
    yield psu
    psu.disable()


@pytest.fixture
def dmm_vout():
    """Simulated DMM for output voltage measurement."""
    class SimDMM:
        def __init__(self):
            # Simulate realistic values
            self._voltage = 3.298  # Slightly under nominal

        def measure_dc_voltage(self):
            return self._voltage

        def set_sim_voltage(self, v):
            self._voltage = v

    return SimDMM()


@pytest.fixture
def dmm_iin():
    """Simulated DMM for input current measurement."""
    class SimDMM:
        def __init__(self):
            self._current = 0.680  # ~680mA at 1A load (93% eff)

        def measure_dc_current(self):
            return self._current

        def set_sim_current(self, i):
            self._current = i

    return SimDMM()


@pytest.fixture
def scope():
    """Simulated oscilloscope for ripple measurement."""
    class SimScope:
        def __init__(self):
            self._ripple_mv = 22.0  # 22mV typical

        def measure_vpp(self, channel=1):
            return self._ripple_mv / 1000  # Return in V

        def set_sim_ripple(self, mv):
            self._ripple_mv = mv

    return SimScope()


@pytest.fixture
def eload():
    """Simulated electronic load."""
    class SimEload:
        def __init__(self):
            self.current = 0.0
            self.mode = "CC"

        def set_current(self, i):
            self.current = i

        def set_mode(self, mode):
            self.mode = mode

    eload = SimEload()
    yield eload
    eload.set_current(0)


# =============================================================================
# Test Functions - Generated from Test Requirements
# =============================================================================


class TestOutputVoltage:
    """Output voltage accuracy tests - Section 7.1."""

    def test_output_voltage_accuracy(self, spec, psu, dmm_vout, eload):
        """Verify output voltage at nominal load (0.5A).

        Datasheet ref: Section 7.1 - Output Voltage Accuracy Test
        Pass criteria: 3.234V <= VOUT <= 3.366V (±1%)
        With 10% guardband: ~3.251V to 3.349V
        """
        harness = TestHarness(
            step_name="test_output_voltage_accuracy",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 0.5}]},
        )

        # Setup
        psu.set_voltage(5.0)
        psu.enable()
        eload.set_current(0.5)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    value = dmm_vout.measure_dc_voltage()
                    harness.measure("output_voltage", value)

        assert step.outcome == Outcome.PASS
        assert step.vectors[0].measurements[0].dut_pin == "TP2"

    def test_output_voltage_full_load(self, spec, psu, dmm_vout, eload):
        """Verify output voltage at full load (3A).

        Datasheet ref: Section 4.2 - Full load condition
        """
        harness = TestHarness(
            step_name="test_output_voltage_full_load",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 3.0}]},
        )

        psu.set_voltage(5.0)
        psu.enable()
        eload.set_current(3.0)

        # Simulate slightly lower voltage at full load (load regulation)
        dmm_vout.set_sim_voltage(3.285)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    value = dmm_vout.measure_dc_voltage()
                    harness.measure("output_voltage", value)

        assert step.outcome == Outcome.PASS


class TestEfficiency:
    """Efficiency tests - Section 7.3."""

    def test_efficiency_1a_load(self, spec, psu, dmm_vout, dmm_iin, eload):
        """Verify efficiency at 1A load.

        Datasheet ref: Section 7.3 - Efficiency Test
        Pass criteria: Efficiency >= 90%

        Calculation:
        - PIN = VIN × IIN
        - POUT = VOUT × IOUT
        - Efficiency = POUT / PIN × 100
        """
        harness = TestHarness(
            step_name="test_efficiency",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "vin": 5.0, "load": 1.0}]},
        )

        psu.set_voltage(5.0)
        psu.enable()
        eload.set_current(1.0)

        # Simulate measurements
        dmm_vout.set_sim_voltage(3.30)
        dmm_iin.set_sim_current(0.710)  # For ~93% efficiency

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    vout = dmm_vout.measure_dc_voltage()
                    iin = dmm_iin.measure_dc_current()

                    # Calculate efficiency
                    vin = 5.0
                    iout = 1.0
                    pin = vin * iin
                    pout = vout * iout
                    eff = (pout / pin) * 100

                    harness.measure("efficiency", Decimal(str(round(eff, 1))))

        assert step.outcome == Outcome.PASS


class TestRipple:
    """Output ripple tests - Section 7.4."""

    def test_output_ripple_full_load(self, spec, psu, scope, eload):
        """Verify output ripple at full load.

        Datasheet ref: Section 7.4 - Output Ripple Test
        Pass criteria: Ripple <= 50mVpp at 3A load
        """
        harness = TestHarness(
            step_name="test_output_ripple",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 3.0}]},
        )

        psu.set_voltage(5.0)
        psu.enable()
        eload.set_current(3.0)

        # Simulate 35mV ripple (typical at full load)
        scope.set_sim_ripple(35.0)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    ripple_v = scope.measure_vpp(channel=1)
                    ripple_mv = ripple_v * 1000
                    harness.measure("output_ripple", Decimal(str(ripple_mv)))

        assert step.outcome == Outcome.PASS


class TestQuiescentCurrent:
    """Quiescent current test."""

    def test_quiescent_current(self, spec, psu, dmm_iin):
        """Verify quiescent current (no load).

        Datasheet ref: Section 4.1 - Quiescent current
        Pass criteria: IQ <= 200µA (with guardband: ~180µA)
        """
        harness = TestHarness(
            step_name="test_quiescent_current",
            spec_context=spec,
            config={"vectors": [{"load": 0}]},
        )

        psu.set_voltage(5.0)
        psu.enable()
        # No load connected

        # Simulate 150µA quiescent
        dmm_iin.set_sim_current(0.000150)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    iq_a = dmm_iin.measure_dc_current()
                    iq_ua = iq_a * 1_000_000
                    harness.measure("quiescent_current", Decimal(str(iq_ua)))

        assert step.outcome == Outcome.PASS


# =============================================================================
# Summary Test - Run All Critical Tests
# =============================================================================


class TestProductionSuite:
    """Production test suite - all critical tests."""

    def test_full_production_sequence(self, spec, psu, dmm_vout, dmm_iin, scope, eload):
        """Run full production test sequence.

        This test demonstrates the complete workflow:
        1. Power up DUT
        2. Measure output voltage
        3. Measure efficiency
        4. Measure ripple
        5. All measurements traced back to spec
        """
        psu.set_voltage(5.0)
        psu.enable()
        eload.set_current(1.0)

        harness = TestHarness(
            step_name="production_test",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 1.0, "vin": 5.0}]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Measure output voltage
                    vout = dmm_vout.measure_dc_voltage()
                    m_vout = harness.measure("output_voltage", vout)

                    # Verify traceability
                    assert m_vout.spec_ref is not None
                    assert "Section 4.2" in m_vout.spec_ref
                    assert m_vout.dut_pin == "TP2"

        # Verify all passed
        assert step.outcome == Outcome.PASS

        # Return step for inspection
        return step

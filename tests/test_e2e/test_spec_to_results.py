"""End-to-end test for the spec → results workflow.

This test verifies the complete datasheet-to-results workflow:
1. Load product spec from YAML (pins, characteristics, conditions)
2. Derive test limits from spec with guardband
3. Execute tests with spec-driven limits
4. Verify results include full traceability (spec_ref, dut_pin, etc.)

The workflow demonstrated:

    power_board.yaml (datasheet representation)
           │
           ▼
    SpecContext (loads and provides access)
           │
           ├──► get_limit("output_voltage", temperature=25, load=0.1)
           │        Returns: Limit(low=3.135, high=3.465, spec_ref="Section 7.2 @ ...")
           │
           ├──► get_pin_info("output_voltage")
           │        Returns: {dut_pin: "J1.3", net: "VOUT_3V3", ...}
           │
           ▼
    TestHarness.measure("output_voltage", value)
           │
           ├──► Auto-resolves limit from spec
           ├──► Auto-populates dut_pin, instrument_channel
           ├──► Checks value against limits
           │
           ▼
    Measurement (in TestVector, in TestStep, in TestRun)
           │
           ├──► spec_ref: "Section 7.2 @ load=0.1, temperature=25"
           ├──► dut_pin: "J1.3"
           ├──► outcome: PASS/FAIL
           │
           ▼
    Parquet Storage (full traceability preserved)
"""

from decimal import Decimal
from pathlib import Path

import pytest

from litmus.data.models import Outcome
from litmus.execution.harness import TestHarness
from litmus.products.context import SpecContext


# Path to demo spec
SPEC_PATH = Path(__file__).parent.parent.parent / "demo" / "specs" / "power_board.yaml"


class TestSpecContext:
    """Test SpecContext loading and limit derivation."""

    def test_load_spec(self):
        """Verify spec loads with all expected fields."""
        spec = SpecContext.from_file(SPEC_PATH)

        assert spec.product.id == "power_board"
        assert spec.product.name == "Demo Power Board"
        assert "output_voltage" in spec.product.characteristics
        assert "VOUT" in spec.product.pins

    def test_get_limit_basic(self):
        """Derive limit from characteristic at specific conditions."""
        spec = SpecContext.from_file(SPEC_PATH)

        limit = spec.get_limit("output_voltage", temperature=25, load=0.1)

        assert limit.nominal == Decimal("3.3")
        assert limit.units == "V"
        # 3.3 ± 5% = [3.135, 3.465]
        assert limit.low == Decimal("3.135")
        assert limit.high == Decimal("3.465")
        assert "Section 7.2" in limit.spec_ref

    def test_get_limit_with_guardband(self):
        """Derive limit with guardband applied."""
        spec = SpecContext.from_file(SPEC_PATH, guardband_pct=Decimal("10"))

        limit = spec.get_limit("output_voltage", temperature=25, load=0.1)

        # Original range: 0.33 (3.135 to 3.465)
        # Guardband 10%: tighten by 0.033 on each side
        # New range: [3.1515, 3.4485]
        assert limit.low > Decimal("3.135")
        assert limit.high < Decimal("3.465")
        # Verify it's approximately 10% tighter
        original_range = Decimal("3.465") - Decimal("3.135")
        new_range = limit.high - limit.low
        reduction = (original_range - new_range) / original_range
        assert Decimal("0.09") < reduction < Decimal("0.11")

    def test_get_limit_no_conditions_uses_first(self):
        """When no conditions specified, uses first condition point."""
        spec = SpecContext.from_file(SPEC_PATH)

        # input_voltage has a single condition with no parameters
        limit = spec.get_limit("input_voltage")

        assert limit.nominal == Decimal("5.0")
        # 5.0 ± 10% = [4.5, 5.5]
        assert limit.low == Decimal("4.5")
        assert limit.high == Decimal("5.5")

    def test_get_limit_invalid_characteristic(self):
        """Raises KeyError for unknown characteristic."""
        spec = SpecContext.from_file(SPEC_PATH)

        with pytest.raises(KeyError) as exc_info:
            spec.get_limit("nonexistent_char")

        assert "nonexistent_char" in str(exc_info.value)

    def test_get_pin_info(self):
        """Get pin information for traceability."""
        spec = SpecContext.from_file(SPEC_PATH)

        pin_info = spec.get_pin_info("output_voltage")

        assert pin_info["pins"] == ["VOUT"]
        assert pin_info["dut_pin"] == "J1.3"
        assert pin_info["net"] == "VOUT_3V3"

    def test_multiple_characteristics_per_pin(self):
        """Verify same pin can have multiple characteristics."""
        spec = SpecContext.from_file(SPEC_PATH)

        # VOUT should have both DC voltage and ripple
        chars = spec.get_all_characteristics_for_pin("VOUT")

        assert "output_voltage" in chars
        assert "output_ripple" in chars


class TestHarnessSpecIntegration:
    """Test TestHarness integration with SpecContext."""

    def test_harness_auto_resolves_limit_from_spec(self):
        """Harness automatically resolves limits from SpecContext."""
        spec = SpecContext.from_file(SPEC_PATH)
        harness = TestHarness(
            step_name="test_output",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 0.1}]},
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Measure output_voltage - should auto-resolve limit
                    m = harness.measure("output_voltage", Decimal("3.30"))

                    assert m.low_limit == Decimal("3.135")
                    assert m.high_limit == Decimal("3.465")
                    assert m.nominal == Decimal("3.3")
                    assert "Section 7.2" in m.spec_ref
                    assert m.outcome == Outcome.PASS

    def test_harness_auto_populates_channel_info(self):
        """Harness automatically populates channel traceability from spec."""
        spec = SpecContext.from_file(SPEC_PATH)
        harness = TestHarness(step_name="test_output", spec_context=spec)

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    m = harness.measure("output_voltage", Decimal("3.30"))

                    # dut_pin should be populated from spec
                    assert m.dut_pin == "J1.3"

    def test_harness_measurement_fails_out_of_spec(self):
        """Measurement outside spec limits results in FAIL outcome."""
        spec = SpecContext.from_file(SPEC_PATH)
        harness = TestHarness(
            step_name="test_output",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 0.1}]},
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # 3.5V is outside [3.135, 3.465]
                    m = harness.measure("output_voltage", Decimal("3.50"))

                    assert m.outcome == Outcome.FAIL

    def test_harness_explicit_limit_overrides_spec(self):
        """Explicit limit parameter overrides spec-derived limit."""
        from litmus.config.models import Limit

        spec = SpecContext.from_file(SPEC_PATH)
        harness = TestHarness(step_name="test_output", spec_context=spec)

        explicit_limit = Limit(
            low=Decimal("3.0"),
            high=Decimal("4.0"),
            units="V",
            spec_ref="OVERRIDE",
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    m = harness.measure("output_voltage", Decimal("3.30"), limit=explicit_limit)

                    assert m.low_limit == Decimal("3.0")
                    assert m.high_limit == Decimal("4.0")
                    assert m.spec_ref == "OVERRIDE"


class TestEndToEndWorkflow:
    """Test complete spec → execution → results workflow."""

    def test_complete_workflow(self):
        """Full workflow: load spec, run test, verify results."""
        spec = SpecContext.from_file(SPEC_PATH)

        # Configure test with conditions from spec
        harness = TestHarness(
            step_name="test_power_board",
            spec_context=spec,
            config={
                "vectors": [
                    {"temperature": 25, "load": 0.1},
                ]
            },
        )

        # Run test
        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Simulate measurements
                    harness.measure("output_voltage", Decimal("3.28"))
                    harness.measure("input_voltage", Decimal("5.01"))

        # Verify results
        assert step.outcome == Outcome.PASS
        assert len(step.vectors) == 1

        tv = step.vectors[0]
        assert tv.outcome == Outcome.PASS
        assert len(tv.measurements) == 2

        # Check output_voltage measurement
        m_out = next(m for m in tv.measurements if m.name == "output_voltage")
        assert m_out.value == Decimal("3.28")
        assert m_out.outcome == Outcome.PASS
        assert m_out.spec_ref is not None
        assert "Section 7.2" in m_out.spec_ref
        assert m_out.dut_pin == "J1.3"

        # Check input_voltage measurement
        m_in = next(m for m in tv.measurements if m.name == "input_voltage")
        assert m_in.value == Decimal("5.01")
        assert m_in.outcome == Outcome.PASS
        assert m_in.dut_pin == "J1.1"

    def test_workflow_with_multiple_vectors(self):
        """Test execution across multiple condition vectors."""
        spec = SpecContext.from_file(SPEC_PATH)

        harness = TestHarness(
            step_name="test_sweep",
            spec_context=spec,
            config={
                "vectors": {
                    "expand": "product",
                    "temperature": [25],
                    "load": [0.1],  # Only condition defined in spec
                }
            },
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    harness.measure("output_voltage", Decimal("3.30"))

        assert step.outcome == Outcome.PASS
        assert len(step.vectors) == 1

    def test_workflow_failure_propagates(self):
        """Failure in measurement propagates to vector and step outcome."""
        spec = SpecContext.from_file(SPEC_PATH)

        harness = TestHarness(
            step_name="test_failing",
            spec_context=spec,
            config={"vectors": [{"temperature": 25, "load": 0.1}]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # This should pass
                    harness.measure("input_voltage", Decimal("5.0"))
                    # This should fail (outside 3.135-3.465 range)
                    harness.measure("output_voltage", Decimal("2.5"))

        # Failure should propagate up
        assert step.vectors[0].outcome == Outcome.FAIL
        assert step.outcome == Outcome.FAIL


class TestMinimalSpec:
    """Test with minimal.yaml to verify simple specs work."""

    def test_minimal_spec_loads(self):
        """Verify minimal spec loads and works."""
        minimal_path = SPEC_PATH.parent / "minimal.yaml"
        spec = SpecContext.from_file(minimal_path)

        assert spec.product.id == "minimal_board"
        assert "VOUT" in spec.product.pins
        assert "output_voltage" in spec.product.characteristics

    def test_minimal_spec_limit_derivation(self):
        """Derive limit from minimal spec."""
        minimal_path = SPEC_PATH.parent / "minimal.yaml"
        spec = SpecContext.from_file(minimal_path)

        limit = spec.get_limit("output_voltage")

        assert limit.nominal == Decimal("5.0")
        # 5.0 ± 10% = [4.5, 5.5]
        assert limit.low == Decimal("4.5")
        assert limit.high == Decimal("5.5")

    def test_minimal_spec_harness(self):
        """Run test with minimal spec."""
        minimal_path = SPEC_PATH.parent / "minimal.yaml"
        spec = SpecContext.from_file(minimal_path)

        harness = TestHarness(step_name="test_minimal", spec_context=spec)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    m = harness.measure("output_voltage", Decimal("5.1"))

                    assert m.outcome == Outcome.PASS
                    assert m.dut_pin == "J1.1"

        assert step.outcome == Outcome.PASS

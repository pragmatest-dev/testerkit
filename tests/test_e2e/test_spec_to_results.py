"""End-to-end test for the spec → results workflow.

This test verifies the complete datasheet-to-results workflow:
1. Load part spec from YAML (pins, characteristics, conditions)
2. Derive test limits from spec with guardband
3. Execute tests with spec-driven limits
4. Verify results include full traceability (spec_ref, dut_pin, etc.)

These tests verify BEHAVIOR, not specific values from example specs.
Example specs can change freely without breaking these tests.
"""

from pathlib import Path

import pytest

from litmus.data.models import Outcome
from litmus.execution.harness import TestHarness
from litmus.models.capability import RangeSpec
from litmus.parts.context import PartContext

# Path to fixture specs (used for integration testing, not value assertions).
# Lives under tests/fixtures so the e2e suite never depends on the layout
# of the example projects under examples/0X-*.
_FIXTURE_DIR = Path(__file__).parent.parent / "fixtures" / "specs"
SPEC_PATH = _FIXTURE_DIR / "power_board_v1.yaml"
MINIMAL_SPEC_PATH = _FIXTURE_DIR / "base_board.yaml"


class TestPartContext:
    """Test PartContext loading and limit derivation behavior."""

    def test_load_spec(self):
        """Verify spec loads with expected structure."""
        spec = PartContext.from_file(SPEC_PATH)

        # Test structure, not specific values
        assert spec.part.id is not None
        assert spec.part.name is not None
        assert len(spec.part.characteristics) > 0
        assert len(spec.part.pins) > 0

    def test_get_limit_returns_valid_limit(self):
        """Derive limit from characteristic - verify structure."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        assert char_id is not None, "Spec should have at least one characteristic with specs"

        limit = spec.get_limit(char_id, **conditions)

        # Verify limit structure
        assert limit.units is not None
        assert limit.spec_ref is not None
        # At least one bound should be set
        assert limit.low is not None or limit.high is not None or limit.nominal is not None

    def test_get_limit_with_guardband_tightens_range(self):
        """Guardband should tighten the limit range."""
        spec_no_gb = PartContext.from_file(SPEC_PATH, guardband_pct=0.0)
        spec_with_gb = PartContext.from_file(SPEC_PATH, guardband_pct=10.0)

        # Find a characteristic with specs that produce a range (value + accuracy)
        char_id, conditions = _find_testable_characteristic(spec_no_gb)

        if char_id is None:
            pytest.fail("No characteristic with tolerance-based limits found")

        limit_no_gb = spec_no_gb.get_limit(char_id, **conditions)
        limit_with_gb = spec_with_gb.get_limit(char_id, **conditions)

        # Guardband should tighten: low increases, high decreases
        if limit_no_gb.low is not None and limit_with_gb.low is not None:
            assert limit_with_gb.low >= limit_no_gb.low
        if limit_no_gb.high is not None and limit_with_gb.high is not None:
            assert limit_with_gb.high <= limit_no_gb.high

    def test_get_limit_invalid_characteristic_raises(self):
        """Raises KeyError for unknown characteristic."""
        spec = PartContext.from_file(SPEC_PATH)

        with pytest.raises(KeyError):
            spec.get_limit("nonexistent_characteristic_xyz")

    def test_get_pin_info_returns_traceability(self):
        """Pin info should include traceability fields."""
        spec = PartContext.from_file(SPEC_PATH)

        # Find a characteristic with pin reference
        char_id = None
        for cid, char in spec.part.characteristics.items():
            pins = spec._get_char_pins(char)
            if pins:
                char_id = cid
                break

        assert char_id is not None, "Spec should have at least one characteristic with pins"

        pin_info = spec.get_pin_info(char_id)

        # Should have traceability fields
        assert "pin" in pin_info or "pins" in pin_info
        assert "dut_pin" in pin_info
        assert "net" in pin_info

    def test_characteristics_reference_defined_pins(self):
        """All pin references in characteristics should exist in part.pins."""
        spec = PartContext.from_file(SPEC_PATH)

        for char_id, char in spec.part.characteristics.items():
            pins = spec._get_char_pins(char)
            for pin_id in pins:
                assert pin_id in spec.part.pins, (
                    f"Characteristic '{char_id}' references undefined pin '{pin_id}'"
                )


class TestHarnessSpecIntegration:
    """Test TestHarness integration with PartContext."""

    def test_harness_resolves_limit_from_spec(self):
        """Harness automatically resolves limits from PartContext."""
        spec = PartContext.from_file(SPEC_PATH)

        # Find a characteristic with conditions
        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        # Get expected limit
        expected_limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_auto_limit",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Signal with a value inside limits
                    test_value = expected_limit.nominal or expected_limit.low or expected_limit.high
                    m = harness.measure(char_id, test_value)

                    # Verify limit was resolved from spec
                    assert m.spec_ref is not None
                    if expected_limit.low:
                        assert m.limit_low == expected_limit.low
                    if expected_limit.high:
                        assert m.limit_high == expected_limit.high

    def test_harness_populates_dut_pin(self):
        """Harness populates dut_pin from spec for traceability."""
        spec = PartContext.from_file(SPEC_PATH)

        # Find characteristic with pin info
        char_id = None
        for cid, char in spec.part.characteristics.items():
            pin_info = spec.get_pin_info(cid)
            if pin_info.get("dut_pin"):
                char_id = cid
                break

        if char_id is None:
            pytest.fail("No characteristic with dut_pin found")

        expected_pin_info = spec.get_pin_info(char_id)
        harness = TestHarness(step_name="test_pin", part_context=spec)

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    m = harness.measure(char_id, 1.0)
                    assert m.dut_pin == expected_pin_info["dut_pin"]

    def test_measurement_pass_when_in_spec(self):
        """Measurement inside limits results in PASS outcome."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_pass",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Use nominal or midpoint of range
                    if limit.nominal:
                        test_value = limit.nominal
                    elif limit.low and limit.high:
                        test_value = (limit.low + limit.high) / 2
                    else:
                        test_value = limit.low or limit.high

                    m = harness.measure(char_id, test_value)
                    assert m.outcome == Outcome.PASSED

    def test_measurement_fail_when_out_of_spec(self):
        """Measurement outside limits results in FAIL outcome."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_fail",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Use value way outside limits
                    if limit.high:
                        test_value = limit.high * 2.0
                    elif limit.low:
                        test_value = limit.low * 0.1
                    else:
                        pytest.fail("No testable limit bounds")

                    m = harness.measure(char_id, test_value)
                    assert m.outcome == Outcome.FAILED

    def test_explicit_limit_overrides_spec(self):
        """Explicit limit parameter overrides spec-derived limit."""
        from litmus.models.test_config import Limit

        spec = PartContext.from_file(SPEC_PATH)

        # Find any characteristic
        char_id = next(iter(spec.part.characteristics.keys()))

        explicit_limit = Limit(
            low=0.0,
            high=100.0,
            units="V",
            spec_ref="EXPLICIT_OVERRIDE",
        )

        harness = TestHarness(step_name="test_override", part_context=spec)

        with harness.step():
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    m = harness.measure(char_id, 50.0, limit=explicit_limit)

                    assert m.limit_low == 0.0
                    assert m.limit_high == 100.0
                    assert m.spec_ref == "EXPLICIT_OVERRIDE"


class TestEndToEndWorkflow:
    """Test complete spec → execution → results workflow."""

    def test_complete_workflow_structure(self):
        """Full workflow produces properly structured results."""
        spec = PartContext.from_file(SPEC_PATH)

        # Find testable characteristics
        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_workflow",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Signal with passing value
                    test_value = limit.nominal or limit.low or limit.high
                    harness.measure(char_id, test_value)

        # Verify result structure
        assert step.outcome in [Outcome.PASSED, Outcome.FAILED]
        assert len(step.vectors) >= 1

        tv = step.vectors[0]
        assert tv.outcome in [Outcome.PASSED, Outcome.FAILED]
        assert len(tv.measurements) >= 1

        m = tv.measurements[0]
        assert m.name == char_id
        assert m.value is not None
        assert m.outcome in [Outcome.PASSED, Outcome.FAILED]

    def test_characteristic_id_populated_in_workflow(self):
        """characteristic_id is populated for spec-driven measurements."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_characteristic_id",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    test_value = limit.nominal or limit.low or limit.high
                    harness.measure(char_id, test_value)

        # Verify characteristic_id is populated
        m = step.vectors[0].measurements[0]
        assert m.characteristic_id == char_id
        assert m.spec_ref is not None

    def test_observations_captured_in_workflow(self):
        """Context observations are captured in TestVector."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_observations",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Add observations via Context
                    harness.context.observe("temp_probe.temperature", 24.8)
                    harness.context.observe("temp_probe.humidity", 45.2)

                    # Signal
                    test_value = limit.nominal or limit.low or limit.high
                    harness.measure(char_id, test_value)

        # Verify observations are captured
        tv = step.vectors[0]
        assert tv.observations["temp_probe.temperature"] == 24.8
        assert tv.observations["temp_probe.humidity"] == 45.2

    def test_failure_propagates_to_step(self):
        """Measurement failure propagates to vector and step outcome."""
        spec = PartContext.from_file(SPEC_PATH)

        char_id, conditions = _find_testable_characteristic(spec)
        if char_id is None:
            pytest.fail("No testable characteristic found")

        limit = spec.get_limit(char_id, **conditions)

        harness = TestHarness(
            step_name="test_propagation",
            part_context=spec,
            config={"vectors": [conditions]},
        )

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Force failure with out-of-spec value
                    if limit.high:
                        bad_value = limit.high * 10.0
                    elif limit.low:
                        bad_value = 0.0
                    else:
                        pytest.fail("No testable limit bounds")

                    harness.measure(char_id, bad_value)

        # Failure should propagate
        assert step.vectors[0].outcome == Outcome.FAILED
        assert step.outcome == Outcome.FAILED


class TestMinimalSpec:
    """Test with minimal spec to verify simple specs work."""

    def test_minimal_spec_loads(self):
        """Minimal spec loads successfully."""
        spec = PartContext.from_file(MINIMAL_SPEC_PATH)

        assert spec.part.id is not None
        assert len(spec.part.characteristics) >= 1
        assert len(spec.part.pins) >= 1

    def test_minimal_spec_limit_derivation(self):
        """Can derive limits from minimal spec."""
        spec = PartContext.from_file(MINIMAL_SPEC_PATH)

        # Get first characteristic
        char_id = next(iter(spec.part.characteristics.keys()))
        limit = spec.get_limit(char_id)

        # Should have valid limit
        assert limit.units is not None
        assert limit.low is not None or limit.high is not None or limit.nominal is not None

    def test_minimal_spec_harness(self):
        """Can run test with minimal spec."""
        spec = PartContext.from_file(MINIMAL_SPEC_PATH)

        char_id = next(iter(spec.part.characteristics.keys()))
        limit = spec.get_limit(char_id)

        harness = TestHarness(step_name="test_minimal", part_context=spec)

        with harness.step() as step:
            for vector in harness.vectors:
                with harness.run_vector(vector):
                    # Signal with in-spec value
                    test_value = limit.nominal or limit.low or limit.high
                    m = harness.measure(char_id, test_value)
                    assert m.outcome == Outcome.PASSED

        assert step.outcome == Outcome.PASSED


def _find_testable_characteristic(spec: PartContext) -> tuple[str | None, dict]:
    """Find a characteristic with bands suitable for testing.

    Returns:
        Tuple of (characteristic_id, conditions_dict) or (None, {}) if not found.
    """
    for char_id, char in spec.part.characteristics.items():
        if not char.bands:
            continue
        # Use the first band's ``when`` clause as the conditions to look up.
        # Scalars pass through; RangeSpec entries collapse to ``.min``.
        band = char.bands[0]
        conditions: dict = {}
        for k, v in band.when.items():
            if isinstance(v, RangeSpec):
                if v.min is not None:
                    conditions[k] = v.min
            else:
                conditions[k] = v
        return char_id, conditions
    return None, {}

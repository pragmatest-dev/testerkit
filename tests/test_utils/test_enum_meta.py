"""Tests for enum metadata registry and abbreviation lookup."""

from litmus.models.capability import ConditionKey
from litmus.models.enums import MeasurementFunction
from litmus.utils.enum_meta import (
    CONDITION_KEY_META,
    MEASUREMENT_FUNCTION_META,
    LookupResult,
    lookup_enum,
    render_enum_reference,
)


class TestRegistryCompleteness:
    """Every enum value has metadata and vice versa."""

    def test_all_measurement_functions_have_meta(self):
        enum_values = {m.value for m in MeasurementFunction}
        meta_keys = set(MEASUREMENT_FUNCTION_META.keys())
        missing = enum_values - meta_keys
        assert not missing, f"Enum values missing metadata: {missing}"

    def test_no_stale_measurement_function_meta(self):
        enum_values = {m.value for m in MeasurementFunction}
        meta_keys = set(MEASUREMENT_FUNCTION_META.keys())
        stale = meta_keys - enum_values
        assert not stale, f"Stale metadata keys (no enum value): {stale}"

    def test_all_condition_keys_have_meta(self):
        enum_values = {c.value for c in ConditionKey}
        meta_keys = set(CONDITION_KEY_META.keys())
        missing = enum_values - meta_keys
        assert not missing, f"Enum values missing metadata: {missing}"

    def test_no_stale_condition_key_meta(self):
        enum_values = {c.value for c in ConditionKey}
        meta_keys = set(CONDITION_KEY_META.keys())
        stale = meta_keys - enum_values
        assert not stale, f"Stale metadata keys (no enum value): {stale}"

    def test_all_meta_entries_have_required_fields(self):
        for value, meta in MEASUREMENT_FUNCTION_META.items():
            assert "abbreviations" in meta, f"{value}: missing abbreviations"
            assert "name" in meta, f"{value}: missing name"
            assert "instrument_classes" in meta, f"{value}: missing instrument_classes"
            assert len(meta["abbreviations"]) > 0, f"{value}: empty abbreviations"

        for value, meta in CONDITION_KEY_META.items():
            assert "abbreviations" in meta, f"{value}: missing abbreviations"
            assert "name" in meta, f"{value}: missing name"
            assert "instrument_classes" in meta, f"{value}: missing instrument_classes"


class TestLookup:
    """Reverse lookup by abbreviation."""

    def test_exact_enum_value(self):
        results = lookup_enum("dc_voltage")
        assert any(r.enum_value == "dc_voltage" for r in results)

    def test_abbreviation_match(self):
        results = lookup_enum("FRES")
        assert len(results) == 1
        assert results[0].enum_value == "resistance_4w"

    def test_case_insensitive(self):
        for term in ["DCV", "dcv", "Dcv"]:
            results = lookup_enum(term)
            assert any(r.enum_value == "dc_voltage" for r in results), f"Failed for {term}"

    def test_ambiguous_q_returns_multiple(self):
        results = lookup_enum("Q")
        values = {r.enum_value for r in results}
        assert "quality_factor" in values
        assert "charge" in values
        assert len(values) >= 2

    def test_ambiguous_dc_returns_both_enums(self):
        results = lookup_enum("DC")
        types = {r.enum_type for r in results}
        # "DC" is abbreviation for duty_cycle in both function and condition enums
        assert "function" in types
        assert "condition" in types

    def test_scpi_abbreviation(self):
        results = lookup_enum("VOLT:DC")
        assert any(r.enum_value == "dc_voltage" for r in results)

    def test_unknown_term_returns_empty(self):
        assert lookup_enum("XYZNONEXISTENT") == []

    def test_result_has_instrument_classes(self):
        results = lookup_enum("FRES")
        assert results[0].instrument_classes == ["dmm", "daq"]

    def test_result_has_matched_on(self):
        results = lookup_enum("FRES")
        assert results[0].matched_on == "FRES"

    def test_searches_both_enums(self):
        """'humidity' exists as both function and condition."""
        results = lookup_enum("humidity")
        types = {r.enum_type for r in results}
        assert "function" in types
        assert "condition" in types

    def test_condition_lookup(self):
        results = lookup_enum("NPLC")
        assert any(r.enum_value == "nplc" and r.enum_type == "condition" for r in results)

    def test_lookup_result_type(self):
        results = lookup_enum("DCV")
        assert all(isinstance(r, LookupResult) for r in results)


class TestMarkdownRenderer:
    """render_enum_reference produces valid markdown."""

    def test_renders_non_empty(self):
        md = render_enum_reference()
        assert len(md) > 100

    def test_contains_headers(self):
        md = render_enum_reference()
        assert "## MeasurementFunction Values" in md
        assert "## ConditionKey Values" in md

    def test_contains_all_functions(self):
        md = render_enum_reference()
        for value in MEASUREMENT_FUNCTION_META:
            assert f"`{value}`" in md

    def test_contains_all_conditions(self):
        md = render_enum_reference()
        for value in CONDITION_KEY_META:
            assert f"`{value}`" in md

    def test_table_format(self):
        md = render_enum_reference()
        assert "|---|" in md

"""Tests for MarkerSpec parsing and YAML range expanders."""

from __future__ import annotations

import pytest

from litmus.config.expanders import expand_ranges
from litmus.config.test_config import MarkerSpec, SidecarConfig, TestEntry


class TestMarkerSpecFromRaw:
    def test_bare_name_string(self) -> None:
        spec = MarkerSpec.from_raw("flaky")
        assert spec == MarkerSpec(name="flaky")

    def test_single_key_dict_with_none_payload(self) -> None:
        spec = MarkerSpec.from_raw({"skip": None})
        assert spec == MarkerSpec(name="skip")

    def test_scalar_payload_becomes_single_positional(self) -> None:
        spec = MarkerSpec.from_raw({"skip": "reason text"})
        assert spec == MarkerSpec(name="skip", args=["reason text"])

    def test_list_payload_spreads_into_args(self) -> None:
        spec = MarkerSpec.from_raw({"parametrize": ["vin", [4.5, 5.0, 5.5]]})
        assert spec == MarkerSpec(name="parametrize", args=["vin", [4.5, 5.0, 5.5]])

    def test_dict_payload_becomes_kwargs(self) -> None:
        spec = MarkerSpec.from_raw({"litmus_limits": {"v_rail": {"tolerance_pct": 5.0}}})
        assert spec == MarkerSpec(
            name="litmus_limits",
            kwargs={"v_rail": {"tolerance_pct": 5.0}},
        )

    def test_numeric_payload_becomes_single_positional(self) -> None:
        spec = MarkerSpec.from_raw({"custom": 42})
        assert spec == MarkerSpec(name="custom", args=[42])

    def test_multi_key_dict_rejected(self) -> None:
        with pytest.raises(ValueError, match="single-key dict"):
            MarkerSpec.from_raw({"skip": "a", "xfail": "b"})

    def test_non_string_name_rejected(self) -> None:
        with pytest.raises(TypeError, match="Marker name must be a string"):
            MarkerSpec.from_raw({42: "oops"})

    def test_top_level_type_rejected(self) -> None:
        with pytest.raises(TypeError, match="string or single-key dict"):
            MarkerSpec.from_raw(42)


class TestMarkerSpecValidator:
    """The before-validator accepts raw YAML shapes when feeding into Pydantic."""

    def test_pydantic_validate_bare_string(self) -> None:
        spec = MarkerSpec.model_validate("flaky")
        assert spec.name == "flaky"
        assert spec.args == []
        assert spec.kwargs == {}

    def test_pydantic_validate_dict_form(self) -> None:
        spec = MarkerSpec.model_validate({"parametrize": ["vin", [1, 2]]})
        assert spec.name == "parametrize"
        assert spec.args == ["vin", [1, 2]]

    def test_pydantic_validate_structured_form_roundtrip(self) -> None:
        spec = MarkerSpec.model_validate(
            {"name": "skipif", "args": ["cond"], "kwargs": {"reason": "r"}}
        )
        assert spec.name == "skipif"
        assert spec.args == ["cond"]
        assert spec.kwargs == {"reason": "r"}


class TestExpandRanges:
    def test_linspace(self) -> None:
        result = expand_ranges({"linspace": [0.0, 1.0, 5]})
        assert result == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])

    def test_arange(self) -> None:
        result = expand_ranges({"arange": [0, 5, 1]})
        assert result == [0, 1, 2, 3, 4]

    def test_logspace(self) -> None:
        result = expand_ranges({"logspace": [0, 2, 3]})
        assert result == pytest.approx([1.0, 10.0, 100.0])

    def test_geomspace(self) -> None:
        result = expand_ranges({"geomspace": [1, 1000, 4]})
        assert result == pytest.approx([1.0, 10.0, 100.0, 1000.0])

    def test_repeat(self) -> None:
        assert expand_ranges({"repeat": [5.0, 3]}) == [5.0, 5.0, 5.0]

    def test_range_two_args(self) -> None:
        assert expand_ranges({"range": [0, 4]}) == [0, 1, 2, 3]

    def test_range_three_args(self) -> None:
        assert expand_ranges({"range": [1, 10, 2]}) == [1, 3, 5, 7, 9]

    def test_unknown_key_passes_through(self) -> None:
        assert expand_ranges({"mystery": [1, 2, 3]}) == {"mystery": [1, 2, 3]}

    def test_multi_key_dict_not_an_expander(self) -> None:
        data = {"linspace": [0, 1, 3], "other": "value"}
        result = expand_ranges(data)
        assert result == data

    def test_scalar_passthrough(self) -> None:
        assert expand_ranges(5.0) == 5.0
        assert expand_ranges("hello") == "hello"
        assert expand_ranges(None) is None

    def test_nested_in_list(self) -> None:
        result = expand_ranges(["vin", {"linspace": [0.0, 1.0, 3]}])
        assert result == ["vin", pytest.approx([0.0, 0.5, 1.0])]

    def test_nested_in_dict_value(self) -> None:
        result = expand_ranges({"parametrize": {"argnames": "vin", "argvalues": {"range": [0, 3]}}})
        assert result == {"parametrize": {"argnames": "vin", "argvalues": [0, 1, 2]}}

    def test_expander_error_wrapped(self) -> None:
        with pytest.raises(ValueError, match="Range expander 'linspace' failed"):
            expand_ranges({"linspace": ["not", "numeric", "args"]})


class TestSidecarConfig:
    def test_empty_is_valid(self) -> None:
        cfg = SidecarConfig()
        assert cfg.markers == []
        assert cfg.tests == {}

    def test_recursive_tree_with_class_branch_and_module_test(self) -> None:
        cfg = SidecarConfig.model_validate(
            {
                "markers": [{"litmus_limits": {"v_rail": {"tolerance_pct": 5.0}}}],
                "tests": {
                    "TestRails": {
                        "markers": [{"parametrize": ["vin", [4.5, 5.0, 5.5]]}],
                        "tests": {
                            "test_rail": {
                                "markers": [{"litmus_limits": {"v_rail": {"tolerance_pct": 1.0}}}],
                            },
                        },
                    },
                    "test_standalone": {
                        "markers": ["flaky"],
                    },
                },
            }
        )
        assert len(cfg.markers) == 1
        assert cfg.markers[0].name == "litmus_limits"
        rails = cfg.tests["TestRails"]
        assert isinstance(rails, TestEntry)
        assert rails.markers[0].name == "parametrize"
        nested = rails.tests["test_rail"]
        assert isinstance(nested, TestEntry)
        assert nested.markers[0].kwargs == {"v_rail": {"tolerance_pct": 1.0}}
        standalone = cfg.tests["test_standalone"]
        assert isinstance(standalone, TestEntry)
        assert standalone.markers[0] == MarkerSpec(name="flaky")
        assert standalone.tests == {}

    def test_rejects_unknown_top_level_key(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"vectors": {}})

    def test_rejects_unknown_test_entry_key(self) -> None:
        with pytest.raises(ValueError, match="extra"):
            SidecarConfig.model_validate({"tests": {"test_x": {"markers": [], "limits": {}}}})

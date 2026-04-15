"""Tests for per-step instrument role aliases."""

from unittest.mock import MagicMock

import pytest
import yaml

from litmus.execution.plugin import (
    InstrumentAccessor,
    _load_step_aliases_and_configs,
    set_current_step_aliases,
)
from litmus.models.config import TestStepConfig


class TestStepConfigAliases:
    """Test that aliases field works on TestStepConfig."""

    def test_aliases_default_empty(self):
        step = TestStepConfig(id="s1", test="tests/test_foo.py::test_bar")
        assert step.aliases == {}

    def test_aliases_parsed(self):
        step = TestStepConfig(
            id="s1",
            test="tests/test_foo.py::test_bar",
            aliases={"dmm": "precision_dmm", "psu": "bench_psu"},
        )
        assert step.aliases == {"dmm": "precision_dmm", "psu": "bench_psu"}


class TestLoadStepAliases:
    """Test _load_step_aliases_and_configs helper."""

    def test_load_from_sequence_file(self, tmp_path):
        seq_file = tmp_path / "sequences" / "my_seq.yaml"
        seq_file.parent.mkdir()
        seq_file.write_text(
            yaml.dump(
                {
                    "id": "my_seq",
                    "description": "test",
                    "steps": [
                        {
                            "id": "step1",
                            "test": "tests/test_a.py::test_one",
                            "aliases": {"dmm": "precision_dmm"},
                        },
                        {
                            "id": "step2",
                            "test": "tests/test_a.py::test_two",
                        },
                        {
                            "id": "step3",
                            "test": "tests/test_b.py::test_three",
                            "aliases": {"dmm": "fast_dmm", "psu": "bench_psu"},
                        },
                    ],
                }
            )
        )

        # Create a mock config object
        config = MagicMock()
        config.getoption.return_value = str(seq_file)
        config.rootpath = tmp_path
        config.invocation_params.dir = str(tmp_path)

        aliases, _configs = _load_step_aliases_and_configs(config)
        assert aliases == {
            "tests/test_a.py::test_one": {"dmm": "precision_dmm"},
            "tests/test_b.py::test_three": {"dmm": "fast_dmm", "psu": "bench_psu"},
        }

    def test_no_sequence_returns_empty(self):
        config = MagicMock()
        config.getoption.return_value = None
        aliases, configs = _load_step_aliases_and_configs(config)
        assert aliases == {}
        assert configs == {}

    def test_missing_file_returns_empty(self, tmp_path):
        config = MagicMock()
        config.getoption.return_value = str(tmp_path / "nonexistent.yaml")
        config.rootpath = tmp_path
        config.invocation_params.dir = str(tmp_path)
        with pytest.warns(UserWarning, match="not found"):
            aliases, configs = _load_step_aliases_and_configs(config)
        assert aliases == {}
        assert configs == {}


class TestAccessorWithAliases:
    """Test InstrumentAccessor resolves through step aliases contextvar."""

    def test_accessor_resolves_alias(self):
        instruments = {"precision_dmm": "fake_precision", "fast_dmm": "fake_fast"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        set_current_step_aliases({"dmm": "precision_dmm"})
        try:
            result = accessor("dmm")
            assert result == "fake_precision"
        finally:
            set_current_step_aliases({})

    def test_accessor_falls_through_without_alias(self):
        instruments = {"dmm": "direct_dmm"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        set_current_step_aliases({})
        try:
            result = accessor("dmm")
            assert result == "direct_dmm"
        finally:
            set_current_step_aliases({})

    def test_accessor_alias_target_missing_raises(self):
        instruments = {"dmm": "some_dmm"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        set_current_step_aliases({"dmm": "nonexistent"})
        try:
            with pytest.raises(KeyError, match="Alias 'dmm' targets 'nonexistent'"):
                accessor("dmm")
        finally:
            set_current_step_aliases({})

    def test_roles_includes_aliases(self):
        instruments = {"precision_dmm": "x", "psu": "y"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        set_current_step_aliases({"dmm": "precision_dmm"})
        try:
            roles = accessor.roles()
            assert "dmm" in roles
            assert "precision_dmm" in roles
            assert "psu" in roles
        finally:
            set_current_step_aliases({})

    def test_alias_deduplication_same_object(self):
        """Two aliases to same role return the same object instance."""
        shared_inst = object()
        instruments = {"precision_dmm": shared_inst}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        set_current_step_aliases({"dmm": "precision_dmm", "meter": "precision_dmm"})
        try:
            assert accessor("dmm") is accessor("meter")
        finally:
            set_current_step_aliases({})

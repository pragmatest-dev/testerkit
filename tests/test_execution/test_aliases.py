"""Tests for per-step instrument role aliases."""

from pathlib import Path
from unittest.mock import MagicMock

import pytest
import yaml

from litmus.config.models import TestStepConfig
from litmus.execution.plugin import (
    InstrumentAccessor,
    _CURRENT_STEP_ALIASES,
    _load_step_aliases,
)


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
    """Test _load_step_aliases helper."""

    def test_load_from_sequence_file(self, tmp_path):
        seq_file = tmp_path / "sequences" / "my_seq.yaml"
        seq_file.parent.mkdir()
        seq_file.write_text(
            yaml.dump(
                {
                    "sequence": {
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
                                # No aliases
                            },
                            {
                                "id": "step3",
                                "test": "tests/test_b.py::test_three",
                                "aliases": {"dmm": "fast_dmm", "psu": "bench_psu"},
                            },
                        ],
                    },
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

        result = _load_step_aliases(config)
        assert result == {
            "tests/test_a.py::test_one": {"dmm": "precision_dmm"},
            "tests/test_b.py::test_three": {"dmm": "fast_dmm", "psu": "bench_psu"},
        }

    def test_no_sequence_returns_empty(self):
        config = MagicMock()
        config.getoption.return_value = None
        assert _load_step_aliases(config) == {}

    def test_missing_file_returns_empty(self, tmp_path):
        config = MagicMock()
        config.getoption.return_value = str(tmp_path / "nonexistent.yaml")
        config.rootpath = tmp_path
        config.invocation_params.dir = str(tmp_path)
        assert _load_step_aliases(config) == {}


class TestAccessorWithAliases:
    """Test InstrumentAccessor resolves through _CURRENT_STEP_ALIASES."""

    def test_accessor_resolves_alias(self):
        import litmus.execution.plugin as plugin

        instruments = {"precision_dmm": "fake_precision", "fast_dmm": "fake_fast"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        # Set alias
        old = plugin._CURRENT_STEP_ALIASES.copy()
        try:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES["dmm"] = "precision_dmm"

            result = accessor("dmm")
            assert result == "fake_precision"
        finally:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES.update(old)

    def test_accessor_falls_through_without_alias(self):
        import litmus.execution.plugin as plugin

        instruments = {"dmm": "direct_dmm"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        old = plugin._CURRENT_STEP_ALIASES.copy()
        try:
            plugin._CURRENT_STEP_ALIASES.clear()
            result = accessor("dmm")
            assert result == "direct_dmm"
        finally:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES.update(old)

    def test_accessor_alias_target_missing_raises(self):
        import litmus.execution.plugin as plugin

        instruments = {"dmm": "some_dmm"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        old = plugin._CURRENT_STEP_ALIASES.copy()
        try:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES["dmm"] = "nonexistent"

            with pytest.raises(KeyError, match="Alias 'dmm' targets 'nonexistent'"):
                accessor("dmm")
        finally:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES.update(old)

    def test_roles_includes_aliases(self):
        import litmus.execution.plugin as plugin

        instruments = {"precision_dmm": "x", "psu": "y"}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        old = plugin._CURRENT_STEP_ALIASES.copy()
        try:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES["dmm"] = "precision_dmm"

            roles = accessor.roles()
            assert "dmm" in roles
            assert "precision_dmm" in roles
            assert "psu" in roles
        finally:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES.update(old)

    def test_alias_deduplication_same_object(self):
        """Two aliases to same role return the same object instance."""
        import litmus.execution.plugin as plugin

        shared_inst = object()
        instruments = {"precision_dmm": shared_inst}
        records = {}
        accessor = InstrumentAccessor(instruments, records)

        old = plugin._CURRENT_STEP_ALIASES.copy()
        try:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES["dmm"] = "precision_dmm"
            plugin._CURRENT_STEP_ALIASES["meter"] = "precision_dmm"

            assert accessor("dmm") is accessor("meter")
        finally:
            plugin._CURRENT_STEP_ALIASES.clear()
            plugin._CURRENT_STEP_ALIASES.update(old)

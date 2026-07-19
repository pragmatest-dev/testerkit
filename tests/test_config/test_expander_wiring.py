"""Wiring check: every YAML read through ``store`` runs ``expand_ranges``."""

from __future__ import annotations

from pathlib import Path

import pytest

from testerkit.store import _read_yaml, load_part


def _write(path: Path, content: str) -> Path:
    path.write_text(content)
    return path


class TestReadYamlWiring:
    def test_expander_fires_at_top_level_list_position(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "cfg.yaml", "values: {linspace: [0.0, 1.0, 5]}\n")
        data = _read_yaml(path)
        assert data["values"] == pytest.approx([0.0, 0.25, 0.5, 0.75, 1.0])

    def test_expander_fires_nested_deeply(self, tmp_path: Path) -> None:
        yaml_src = """
        a:
          b:
            c:
              - vin
              - {range: [0, 3]}
        """
        path = _write(tmp_path / "cfg.yaml", yaml_src)
        data = _read_yaml(path)
        assert data["a"]["b"]["c"] == ["vin", [0, 1, 2]]

    def test_unknown_single_key_dict_passes_through(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "cfg.yaml", "values: {mystery: [1, 2, 3]}\n")
        data = _read_yaml(path)
        assert data["values"] == {"mystery": [1, 2, 3]}

    def test_scalar_and_plain_list_untouched(self, tmp_path: Path) -> None:
        path = _write(tmp_path / "cfg.yaml", "name: hello\nnums: [1, 2, 3]\n")
        data = _read_yaml(path)
        assert data == {"name": "hello", "nums": [1, 2, 3]}


class TestLoadPartWiring:
    def test_when_clause_list_expands_through_pydantic(self, tmp_path: Path) -> None:
        """A ``when:`` clause with a list-expander resolves to an expanded list
        by the time Pydantic validates, and the part loads without error."""
        yaml_src = """
        id: demo
        name: Demo Part
        characteristics:
          rail:
            function: dc_voltage
            direction: output
            unit: V
            pin: TP
            bands:
              - when: {load: {linspace: [0.1, 0.8, 4]}}
                value: 3.3
                accuracy: {pct_reading: 2.0}
        """
        path = _write(tmp_path / "demo.yaml", yaml_src)
        part = load_part(path)
        band = part.characteristics["rail"].bands[0]
        assert band.when["load"] == pytest.approx([0.1, 0.3333333, 0.5666666, 0.8])

"""Anti-drift guard for the AI-facing test-writing surfaces (#66).

The generated CLAUDE.md, the `litmus refs`, and the MCP templates tell a
generative AI how to write Litmus tests. They drifted from the real API once
(a phantom `logger.measure` verb, `psu`/`dmm` assumed without a station,
limit-less `verify`, sidecar `ref:`/`vectors:`/dict-`mocks:`, deleted
`sequence` refs) — and nothing caught it because nothing *ran* the examples.

This test runs / validates the canonical snippets against the real plugin +
models, so a verb rename or a schema change breaks CI instead of silently
shipping broken advice to users.
"""

from __future__ import annotations

from pathlib import Path

import litmus

_SKILLS = Path(litmus.__file__).parent / "skills"
_MCP = Path(litmus.__file__).parent / "mcp"


# ── The verbs the surfaces name must actually exist ────────────────────────


def test_record_only_verbs_exist() -> None:
    """observe/measure/stream are the real record-only verbs; there is no
    `logger` object (the docs' old `logger.measure` was always a phantom)."""
    from litmus import verbs

    assert set(verbs.__all__) == {"observe", "verify", "measure", "stream"}
    assert not hasattr(litmus, "logger")


# ── The zero-config examples from the generated CLAUDE.md must RUN ─────────


def test_generated_claude_md_zero_config_verify(verify) -> None:
    """`verify` with an inline limit — the documented no-config example."""
    verify("output_voltage", 3.3, limit={"low": 3.0, "high": 3.6, "unit": "V"})


def test_generated_claude_md_zero_config_observe(observe) -> None:
    """`observe` — record-only, no limit, no station, no part spec."""
    observe("rail_voltage", 3.28)


# ── The sidecar shapes the templates/refs document must validate ──────────


def test_canonical_sidecar_shapes_validate() -> None:
    """The shapes shown in the MCP TEST_TEMPLATE / datasheet-to-test / refs
    must pass SidecarConfig (extra=forbid) — catches a drift back to `ref:`,
    `vectors:`, or dict-`mocks:`."""
    from litmus.models.test_config import SidecarConfig

    SidecarConfig.model_validate(
        {
            "sweeps": [{"vin": [4.5, 5.0, 5.5]}],
            "limits": {
                "output_voltage": {"characteristic": "output_voltage", "guardband_pct": 10},
                "efficiency": {"low": 55, "high": 100, "unit": "%"},
            },
            "mocks": [{"target": "dmm.measure_dc_voltage", "return_value": 5.0}],
        }
    )


# ── Targeted guards for the specific stale tokens this branch removed ─────


def test_no_phantom_logger_measure_in_surfaces() -> None:
    for md in _SKILLS.rglob("*.md"):
        assert "logger.measure" not in md.read_text(), f"phantom verb resurfaced in {md.name}"


def test_no_deleted_sequence_schema_refs_in_mcp() -> None:
    for py in _MCP.glob("*.py"):
        text = py.read_text()
        assert 'yaml_type="sequence"' not in text, f"deleted 'sequence' schema ref in {py.name}"
        assert "yaml_type='sequence'" not in text, f"deleted 'sequence' schema ref in {py.name}"

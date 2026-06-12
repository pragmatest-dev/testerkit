"""Part YAML resolution chain for the ``part`` fixture.

Single ``--part`` flag with path-or-id dispatch (matches
``--station`` / ``--fixture`` shape):

* ``--part=<id>`` — looks up ``parts/<id>.yaml`` (filename
  match, same shape as ``--station=<id>`` resolving to
  ``stations/<id>.yaml``).
* ``--part=<path>`` — values containing ``/`` or ending in
  ``.yaml``/``.yml`` are loaded as explicit paths.
* ``--uut-part-number=<pn>`` — content match against
  ``part.part_number:`` for auto-discovery.
* Single-file fallback when ``parts/`` holds exactly one YAML.
"""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


_INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.pytest_plugin
    asyncio_default_fixture_loop_scope = function
    """
)


def _make_part_yaml(pytester: pytest.Pytester, name: str, *, part_number: str = "PN-XX") -> None:
    """Write a minimal part YAML under ``parts/<name>.yaml``."""
    parts_dir = pytester.path / "parts"
    parts_dir.mkdir(exist_ok=True)
    (parts_dir / f"{name}.yaml").write_text(
        textwrap.dedent(
            f"""
            id: {name}
            name: {name.replace("_", " ").title()}
            revision: A
            part_number: {part_number}
            characteristics:
              v_rail:
                function: dc_voltage
                direction: output
                units: V
                pin: TP_VOUT
                bands:
                  - value: 3.3
            pins:
              TP_VOUT:
                name: TP1
                net: VOUT_3V3
            """
        )
    )


def test_part_id_lookup_picks_matching_file(pytester: pytest.Pytester) -> None:
    """``--part=<id>`` selects ``parts/<id>.yaml`` even when siblings exist."""
    pytester.makeini(_INI)
    _make_part_yaml(pytester, "alpha")
    _make_part_yaml(pytester, "beta")
    pytester.makepyfile(
        test_pick=textwrap.dedent(
            """
            def test_picks_alpha(part):
                assert part is not None
                assert part.id == "alpha"
            """
        )
    )
    result = pytester.runpytest("-v", "--part=alpha")
    result.assert_outcomes(passed=1)


def test_part_id_missing_yaml_raises_usage_error(pytester: pytest.Pytester) -> None:
    """``--part=<id>`` with no matching file errors instead of silently falling through."""
    pytester.makeini(_INI)
    _make_part_yaml(pytester, "alpha")
    pytester.makepyfile(
        test_missing=textwrap.dedent(
            """
            def test_unreachable(part):
                assert False, "fixture resolution should have failed"
            """
        )
    )
    result = pytester.runpytest("-v", "--part=does_not_exist")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "does_not_exist" in combined
    assert "--part" in combined


def test_part_accepts_path_shape(pytester: pytest.Pytester) -> None:
    """``--part=<path>`` (containing ``/`` or ``.yaml``) loads the path directly."""
    pytester.makeini(_INI)
    _make_part_yaml(pytester, "beta")
    pytester.makepyfile(
        test_path=textwrap.dedent(
            """
            def test_loads(part):
                assert part.id == "beta"
            """
        )
    )
    result = pytester.runpytest("-v", "--part=parts/beta.yaml")
    result.assert_outcomes(passed=1)

"""Product YAML resolution chain for the ``product_context`` fixture.

Mirrors the lookup/path pair pattern of station/fixture flags:

* ``--spec=<path>`` — explicit YAML path (highest precedence).
* ``--product=<id>`` — looks up ``products/<id>.yaml`` (filename match,
  same shape as ``--station=<id>`` resolving to ``stations/<id>.yaml``).
* ``--dut-part-number=<pn>`` — content match against
  ``product.part_number:`` for auto-discovery.
* Single-file fallback when ``products/`` holds exactly one YAML.
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


def _make_product_yaml(pytester: pytest.Pytester, name: str, *, part_number: str = "PN-XX") -> None:
    """Write a minimal product YAML under ``products/<name>.yaml``."""
    products_dir = pytester.path / "products"
    products_dir.mkdir(exist_ok=True)
    (products_dir / f"{name}.yaml").write_text(
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


def test_product_id_lookup_picks_matching_file(pytester: pytest.Pytester) -> None:
    """``--product=<id>`` selects ``products/<id>.yaml`` even when siblings exist."""
    pytester.makeini(_INI)
    _make_product_yaml(pytester, "alpha")
    _make_product_yaml(pytester, "beta")
    pytester.makepyfile(
        test_pick=textwrap.dedent(
            """
            def test_picks_alpha(product_context):
                assert product_context is not None
                assert product_context.product.id == "alpha"
            """
        )
    )
    result = pytester.runpytest("-v", "--product=alpha")
    result.assert_outcomes(passed=1)


def test_product_id_missing_yaml_raises_usage_error(pytester: pytest.Pytester) -> None:
    """``--product=<id>`` with no matching file errors instead of silently falling through."""
    pytester.makeini(_INI)
    _make_product_yaml(pytester, "alpha")
    pytester.makepyfile(
        test_missing=textwrap.dedent(
            """
            def test_unreachable(product_context):
                assert False, "fixture resolution should have failed"
            """
        )
    )
    result = pytester.runpytest("-v", "--product=does_not_exist")
    assert result.ret != 0
    combined = "\n".join(result.outlines + result.errlines)
    assert "does_not_exist" in combined
    assert "--product" in combined


def test_spec_path_beats_product_id(pytester: pytest.Pytester) -> None:
    """``--spec=<path>`` wins over ``--product=<id>`` when both are passed."""
    pytester.makeini(_INI)
    _make_product_yaml(pytester, "alpha")
    _make_product_yaml(pytester, "beta")
    pytester.makepyfile(
        test_path_wins=textwrap.dedent(
            """
            def test_spec_wins(product_context):
                assert product_context.product.id == "beta"
            """
        )
    )
    result = pytester.runpytest("-v", "--spec=products/beta.yaml", "--product=alpha")
    result.assert_outcomes(passed=1)

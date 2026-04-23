"""Integration tests for --litmus-profile using pytester."""

from __future__ import annotations

import textwrap

import pytest

pytest_plugins = ["pytester"]


INI = textwrap.dedent(
    """
    [pytest]
    addopts = -p no:litmus -p litmus.execution.plugin
    """
)


def _write_project(pytester: pytest.Pytester, profiles_yaml: str) -> None:
    """Write a minimal litmus.yaml with the given profiles: block."""
    pytester.makeini(INI)
    litmus_yaml = (
        textwrap.dedent(
            """
        name: pytester_project
        default_station: station
        """
        )
        + profiles_yaml
    )
    (pytester.path / "litmus.yaml").write_text(litmus_yaml)


def test_profile_vectors_replace_sidecar_for_matched_node(pytester: pytest.Pytester) -> None:
    """Active profile's vectors for a test's node-id replace sidecar vectors."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              short:
                vectors:
                  "test_seq.py::TestSeq::test_sweep":
                    vin: [5.0]
            """
        ),
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            vectors:
              methods:
                test_sweep:
                  list:
                    - {vin: 4.5}
                    - {vin: 5.0}
                    - {vin: 5.5}
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_sweep(self, context):
                    assert context.get_param("vin") == 5.0
            """
        )
    )

    # Without profile: 3 vectors
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1, failed=2)

    # With profile=short: 1 vector (just 5.0)
    result = pytester.runpytest("-v", "--litmus-profile=short")
    result.assert_outcomes(passed=1)


def test_profile_limits_beat_sidecar(pytester: pytest.Pytester) -> None:
    """Profile limits for a matched node-id override sidecar limits."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              strict:
                limits:
                  "test_seq.py::TestSeq::test_voltage":
                    v: {low: 3.25, high: 3.35, units: V}
            """
        ),
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            limits:
              v: {low: 3.0, high: 3.6, units: V}
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_voltage(self, verify):
                    # 3.5 is inside sidecar limit (3.0..3.6) but outside profile strict (3.25..3.35)
                    verify("v", 3.5)
            """
        )
    )

    # Without profile: 3.5 passes (inside sidecar)
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=1)

    # With strict profile: 3.5 fails (outside profile limit)
    result = pytester.runpytest("-v", "--litmus-profile=strict")
    result.assert_outcomes(failed=1)


def test_profile_injects_skip_marker(pytester: pytest.Pytester) -> None:
    """Profile can add pytest.mark.skip to a matched node-id."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              smoke:
                markers:
                  "test_seq.py::TestSeq::test_slow":
                    - skip: "too slow for smoke"
            """
        ),
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_fast(self):
                    pass

                def test_slow(self):
                    pass
            """
        )
    )

    # No profile: both run
    result = pytester.runpytest("-v")
    result.assert_outcomes(passed=2)

    # smoke profile: test_slow is skipped
    result = pytester.runpytest("-v", "--litmus-profile=smoke")
    result.assert_outcomes(passed=1, skipped=1)


def test_profile_keyword_filter_narrows_collection(pytester: pytest.Pytester) -> None:
    """profile.pytest.keyword acts like -k on the session."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              only_rails:
                pytest:
                  keyword: "rails"
            """
        ),
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_rails(self):
                    pass

                def test_efficiency(self):
                    pass
            """
        )
    )

    result = pytester.runpytest("-v", "--litmus-profile=only_rails")
    result.assert_outcomes(passed=1, deselected=1)


def test_profile_keyword_composes_with_cli_k(pytester: pytest.Pytester) -> None:
    """When both profile.keyword and CLI -k are set, they AND-compose."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              only_tests:
                pytest:
                  keyword: "test_"
            """
        ),
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_rails(self):
                    pass

                def test_efficiency(self):
                    pass
            """
        )
    )

    result = pytester.runpytest("-v", "--litmus-profile=only_tests", "-k", "rails")
    result.assert_outcomes(passed=1, deselected=1)


def test_unknown_profile_name_errors(pytester: pytest.Pytester) -> None:
    """An unknown profile name raises a clean UsageError."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              validation: {}
            """
        ),
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_x():
                pass
            """
        )
    )

    result = pytester.runpytest("--litmus-profile=ghost")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*Unknown --litmus-profile*'ghost'*"])


def test_no_profile_flag_is_baseline(pytester: pytest.Pytester) -> None:
    """With no --litmus-profile, behavior is unchanged."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              production:
                vectors:
                  "test_seq.py::TestSeq::test_sweep":
                    vin: [4.5, 5.0, 5.5]
            """
        ),
    )
    (pytester.path / "test_seq.yaml").write_text(
        textwrap.dedent(
            """
            vectors:
              methods:
                test_sweep:
                  list:
                    - {vin: 5.0}
            """
        )
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_sweep(self, context):
                    assert context.get_param("vin") == 5.0
            """
        )
    )

    result = pytester.runpytest("-v")
    # Sidecar wins when no profile is active: 1 vector
    result.assert_outcomes(passed=1)


def test_profile_glob_pattern_matches_method(pytester: pytest.Pytester) -> None:
    """fnmatch glob in profile keys matches node-ids."""
    _write_project(
        pytester,
        textwrap.dedent(
            """
            profiles:
              allskip:
                markers:
                  "test_seq.py::TestSeq::*":
                    - skip: "match-all"
            """
        ),
    )
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            class TestSeq:
                def test_one(self):
                    pass

                def test_two(self):
                    pass
            """
        )
    )

    result = pytester.runpytest("-v", "--litmus-profile=allskip")
    result.assert_outcomes(skipped=2)


# ---------------------------------------------------------------------------
# Facet-based selection: --<facet>=<value> auto-synthesized from profiles
# ---------------------------------------------------------------------------


_FACET_PROJECT = textwrap.dedent(
    """
    profiles:
      validation_pb:
        facets: {product: power_board, phase: validation}
        markers:
          "test_seq.py::*":
            - skip: "validation_pb active"
      prod_pb:
        facets: {product: power_board, phase: production}
        markers:
          "test_seq.py::*":
            - skip: "prod_pb active"
      validation_mb:
        facets: {product: main_board, phase: validation}
        markers:
          "test_seq.py::*":
            - skip: "validation_mb active"
    """
)


def _write_facet_project(pytester: pytest.Pytester) -> None:
    _write_project(pytester, _FACET_PROJECT)
    pytester.makepyfile(
        test_seq=textwrap.dedent(
            """
            def test_one():
                pass
            """
        )
    )


def test_facet_query_selects_unique_profile(pytester: pytest.Pytester) -> None:
    """Passing --product + --phase picks the one matching profile."""
    _write_facet_project(pytester)
    result = pytester.runpytest("-v", "--product=power_board", "--phase=validation")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*validation_pb active*"])


def test_facet_query_ambiguous_errors(pytester: pytest.Pytester) -> None:
    """Facet query matching >1 profile raises UsageError."""
    _write_facet_project(pytester)
    result = pytester.runpytest("--product=power_board")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*Facet query is ambiguous*validation_pb*prod_pb*"])


def test_facet_query_no_match_errors(pytester: pytest.Pytester) -> None:
    """Facet query with zero matches raises UsageError listing available combos."""
    _write_facet_project(pytester)
    result = pytester.runpytest("--product=ghost_board")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*No profile matches the facet query*ghost_board*"])


def test_name_and_facet_cross_check_mismatch_errors(pytester: pytest.Pytester) -> None:
    """Profile name + facet flag that disagrees raises UsageError."""
    _write_facet_project(pytester)
    result = pytester.runpytest("--litmus-profile=validation_pb", "--product=main_board")
    assert result.ret != 0
    result.stderr.fnmatch_lines(["*does not match facet flags*product=*main_board*"])


def test_name_and_facet_cross_check_match_ok(pytester: pytest.Pytester) -> None:
    """Profile name + facet flag that agrees selects the profile."""
    _write_facet_project(pytester)
    result = pytester.runpytest("-v", "--litmus-profile=validation_pb", "--product=power_board")
    result.assert_outcomes(skipped=1)
    result.stdout.fnmatch_lines(["*validation_pb active*"])

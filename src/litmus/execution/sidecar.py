"""Sidecar YAML loading + ``litmus_limits`` marker resolution.

Two responsibilities live here:

* **Sidecar loading** (:func:`load_sidecar`, :func:`sidecar_markers_for`).
  Parse the ``<module>.yaml`` sitting next to a test module and merge
  its file-level / class / per-test markers into a single ordered list
  for the pytest plugin to attach during collection.
* **Limit resolution** (:class:`_LimitRef`, :class:`_PolicyLimit`,
  :class:`_BandSet`, :func:`parse_limits_block`, :func:`resolve_limits`,
  :func:`match_band`). Walk a merged ``litmus_limits`` payload and turn
  each entry into either a concrete :class:`Limit` (resolved now) or a
  deferred form (``ref:``, policy shape, or condition-indexed bandset)
  that the logger picks the matching band from at measurement time.

Both lived in ``plugin.py`` originally; pulled out so the pytest hooks
file stays focused on hook registration.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
import yaml

from litmus.config.expanders import expand_ranges
from litmus.config.test_config import (
    Limit,
    MarkerSpec,
    MeasurementLimitConfig,
    SidecarConfig,
    TestEntry,
)

if TYPE_CHECKING:
    from litmus.models.project import ProfileConfig
from litmus.execution._state import (
    get_active_profile,
    get_active_spec_context,
    get_active_vector_params,
)


@functools.cache
def load_sidecar(module_file: Path) -> SidecarConfig | None:
    """Return parsed ``<module>.yaml`` next to ``module_file`` or ``None``.

    Cached on ``module_file`` so a module with many parametrize cases
    parses its YAML once per session instead of once per test. The
    returned :class:`SidecarConfig` is immutable from the caller's
    perspective — its shape mirrors pytest's node-id structure
    (file-level ``markers`` + recursive ``tests:`` tree).
    """
    yaml_path = module_file.with_suffix(".yaml")
    if not yaml_path.exists():
        return None
    with yaml_path.open() as fh:
        data = yaml.safe_load(fh) or {}
    if not isinstance(data, dict):
        raise ValueError(
            f"{yaml_path} must contain a mapping at the top level; got {type(data).__name__}"
        )
    data = expand_ranges(data)
    return SidecarConfig.model_validate(data)


def sidecar_markers_for(
    root: SidecarConfig | ProfileConfig | TestEntry | None,
    cls_name: str | None,
    func_name: str | None,
) -> list[MarkerSpec]:
    """Walk a sidecar-shaped tree to collect markers for one test.

    Yields, in order: root ``markers`` (file-level) → if the test is a
    method, the class branch's ``markers`` → the leaf's ``markers``.
    Leaf lookup precedence (most → least specific):

    1. nested ``tests[Class].tests[method]`` (preferred form)
    2. dotted shorthand ``tests["Class.method"]`` at the root
    3. bare ``tests[method]`` shorthand at the root

    The first match wins; class-branch markers still apply when the leaf
    came from a dotted or bare shorthand. Accepts either a
    :class:`SidecarConfig` (sidecar root) or a :class:`ProfileConfig`
    (structural duck typing — both expose ``markers`` + ``tests``).
    """
    if root is None:
        return []
    out: list[MarkerSpec] = list(root.markers)
    if cls_name is not None:
        class_branch = root.tests.get(cls_name)
        if class_branch is not None:
            out.extend(class_branch.markers)
            if func_name is not None:
                method_leaf = class_branch.tests.get(func_name)
                if method_leaf is not None:
                    out.extend(method_leaf.markers)
                    return out
    if func_name is None:
        return out
    if cls_name is not None:
        dotted_leaf = root.tests.get(f"{cls_name}.{func_name}")
        if dotted_leaf is not None:
            out.extend(dotted_leaf.markers)
            return out
    bare_leaf = root.tests.get(func_name)
    if bare_leaf is not None:
        out.extend(bare_leaf.markers)
    return out


class _LimitRef:
    """Placeholder for ``limits.<name>.ref: <product_char_id>``.

    Resolved at push time by looking up the product spec via the active
    :class:`SpecContext`; swallows a missing spec / missing
    characteristic silently (the measurement just records unchecked).
    """

    __slots__ = ("target",)

    def __init__(self, target: str) -> None:
        self.target = target


# Keys that signal an entry is a :class:`MeasurementLimitConfig` policy —
# direct Limit entries use ``low`` / ``high`` / ``nominal`` / ``units`` only.
_POLICY_LIMIT_FIELDS = frozenset({"characteristic", "tolerance_pct", "tolerance_abs"})


class _PolicyLimit:
    """Policy-limit entry deferred until push time.

    Carries the raw :class:`MeasurementLimitConfig` plus the test-level
    characteristic (from ``sidecar.tests.<method>.characteristic``) so
    the resolver can derive a concrete :class:`Limit` from the product
    spec + active vector params.
    """

    __slots__ = ("config", "test_char")

    def __init__(self, config: MeasurementLimitConfig, test_char: str | None) -> None:
        self.config = config
        self.test_char = test_char


class _BandSet:
    """Condition-indexed list of limit bands deferred until measurement time.

    Carries a list of ``(when, entry)`` pairs where each ``entry`` is
    itself a parsed band (``Limit`` / :class:`_LimitRef` / :class:`_PolicyLimit`).
    At measurement time the logger picks the first band whose ``when``
    matches the active vector params (same logic as ``SpecBand.when`` via
    :func:`band_matches`). No match → ``pytest.UsageError``.
    """

    __slots__ = ("bands",)

    def __init__(
        self,
        bands: list[tuple[dict[str, Any], Limit | _LimitRef | _PolicyLimit]],
    ) -> None:
        self.bands = bands


def _parse_limit_entry(
    spec: Mapping[str, Any],
    *,
    test_char: str | None,
) -> Limit | _LimitRef | _PolicyLimit:
    """Parse a single limit mapping into its deferred-or-resolved form."""
    from litmus.execution.logger import _limit_from_dict

    if "ref" in spec:
        return _LimitRef(spec["ref"])
    if _POLICY_LIMIT_FIELDS & spec.keys():
        return _PolicyLimit(MeasurementLimitConfig.model_validate(dict(spec)), test_char)
    return _limit_from_dict(spec)


def parse_limits_block(
    raw: Mapping[str, Any] | None,
    *,
    test_char: str | None = None,
) -> dict[str, Limit | _LimitRef | _PolicyLimit | _BandSet]:
    """Convert a sidecar ``limits:`` mapping into Limit / reference / policy / bandset objects.

    Entries with ``ref:`` become :class:`_LimitRef`. Entries with any of
    :data:`_POLICY_LIMIT_FIELDS` become :class:`_PolicyLimit` wrapping a
    :class:`MeasurementLimitConfig` (resolution deferred to push time so
    the active vector params + spec context are in scope). A list-valued
    entry is parsed as :class:`_BandSet` — condition-indexed bands matched
    at measurement time via the entry's ``when:`` keys. Everything else
    is treated as a direct :class:`Limit`.
    """
    if not raw:
        return {}
    out: dict[str, Limit | _LimitRef | _PolicyLimit | _BandSet] = {}
    for name, spec in raw.items():
        if isinstance(spec, list):
            bands: list[tuple[dict[str, Any], Limit | _LimitRef | _PolicyLimit]] = []
            for band_spec in spec:
                if not isinstance(band_spec, Mapping):
                    raise ValueError(
                        f"limits.{name!r} bands must be mappings; got {type(band_spec).__name__}"
                    )
                when = dict(band_spec.get("when") or {})
                body = {k: v for k, v in band_spec.items() if k != "when"}
                bands.append((when, _parse_limit_entry(body, test_char=test_char)))
            out[name] = _BandSet(bands)
            continue
        if not isinstance(spec, Mapping):
            raise ValueError(
                f"limits.{name!r} must be a mapping or list; got {type(spec).__name__}"
            )
        out[name] = _parse_limit_entry(spec, test_char=test_char)
    return out


def _resolve_entry(
    value: Limit | _LimitRef | _PolicyLimit,
    *,
    spec: Any,
    params: dict[str, Any],
    guardband_pct: float,
) -> Limit | None:
    """Resolve a single parsed limit entry to a concrete :class:`Limit`.

    Shared by :func:`resolve_limits` (push-time) and :func:`match_band`
    (measurement-time). Returns ``None`` if the entry can't be resolved
    (missing spec / characteristic).
    """
    from litmus.execution.limits import _apply_guardband
    from litmus.models.config import Comparator
    from litmus.models.config import Limit as LimitModel

    if isinstance(value, _LimitRef):
        if spec is None:
            return None
        try:
            return spec.get_limit(value.target, guardband_pct=guardband_pct, **params)
        except (KeyError, ValueError):
            return None

    if isinstance(value, _PolicyLimit):
        cfg = value.config
        char_id = cfg.characteristic or value.test_char
        if char_id is None or spec is None:
            return None
        char = spec.product.characteristics.get(char_id)
        if char is None:
            return None
        band = char.get_spec_at(dict(params))
        if band is None or not isinstance(band.value, (int, float)):
            return None
        nominal = float(band.value)
        if cfg.tolerance_pct is not None:
            delta = abs(nominal) * cfg.tolerance_pct / 100.0
        elif cfg.tolerance_abs is not None:
            delta = float(cfg.tolerance_abs)
        else:
            return None
        low, high = nominal - delta, nominal + delta
        low, high = _apply_guardband(low, high, guardband_pct, Comparator.GELE.value)
        return LimitModel(
            low=low,
            high=high,
            nominal=nominal,
            units=cfg.units or char.units or "",
            spec_id=char_id,
            spec_ref=char_id,
            comparator=Comparator.GELE,
        )

    return value


def resolve_limits(
    raw_map: Mapping[str, Limit | _LimitRef | _PolicyLimit | _BandSet],
) -> dict[str, Limit | _BandSet]:
    """Resolve deferred entries against the active spec + vector params.

    Literal :class:`Limit` entries pass through unchanged. :class:`_LimitRef`
    entries look up the named characteristic on the active spec.
    :class:`_PolicyLimit` entries derive a :class:`Limit` from
    ``MeasurementLimitConfig`` policy fields (``tolerance_pct`` /
    ``tolerance_abs``) against ``product.characteristics[char]
    .get_spec_at(active_vector_params).value``, layered with
    ``profile.guardband_pct``. :class:`_BandSet` entries pass through
    as-is — band matching happens at measurement time against the
    current active vector params (needed so self-loop tests resolve a
    distinct band per iteration). Entries that can't be resolved (no
    spec, missing characteristic) are dropped so the measurement records
    unchecked.
    """
    resolved: dict[str, Limit | _BandSet] = {}
    spec = get_active_spec_context()
    profile = get_active_profile()
    guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0
    params = get_active_vector_params()

    for name, value in raw_map.items():
        if isinstance(value, _BandSet):
            resolved[name] = value
            continue
        result = _resolve_entry(value, spec=spec, params=params, guardband_pct=guardband_pct)
        if result is not None:
            resolved[name] = result
    return resolved


def match_band(
    bandset: _BandSet,
    active_params: Mapping[str, Any],
) -> Limit:
    """Pick the matching band and resolve it to a concrete :class:`Limit`.

    Iterates ``bandset.bands`` in order; picks the first whose ``when:``
    matches ``active_params`` using :func:`band_matches` (the same logic
    that ``ProductCharacteristic.get_spec_at`` uses). Raises
    ``pytest.UsageError`` when no band matches — silent skips of a
    declared limit would hide bugs.
    """
    from litmus.config.capability import SpecBand, band_matches

    spec_ctx = get_active_spec_context()
    profile = get_active_profile()
    guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0
    params = dict(active_params)

    for when, entry in bandset.bands:
        # Reuse SpecBand.when semantics by constructing a synthetic band.
        probe = SpecBand.model_validate({"when": when}) if when else SpecBand(when={})
        if band_matches(probe, params):
            resolved = _resolve_entry(
                entry, spec=spec_ctx, params=params, guardband_pct=guardband_pct
            )
            if resolved is None:
                raise pytest.UsageError(
                    f"Limit band matched (when={when!r}) but resolution yielded no Limit "
                    "(missing spec context or characteristic)."
                )
            return resolved

    raise pytest.UsageError(
        f"No limit band matched active params {params!r}. "
        f"Declared bands: {[dict(w) for w, _ in bandset.bands]!r}"
    )

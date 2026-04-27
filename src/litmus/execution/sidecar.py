"""Sidecar YAML loading + ``limits`` resolution.

Two responsibilities live here:

* **Sidecar loading** (:func:`load_sidecar`, :func:`merged_test_entry`).
  Parse the ``<module>.yaml`` sitting next to a test module and merge
  its file-level / class / per-test fields into a single typed
  :class:`TestEntry` for the pytest plugin to attach during collection.
* **Limit resolution** (:func:`resolve_limit`).
  Walk a merged ``limits`` mapping (typed :class:`MeasurementLimitConfig`
  per measurement) and turn each entry into a concrete :class:`Limit`
  against the active spec context + vector params, including
  condition-indexed ``bands:`` with sibling-as-catch-all fallback.

The schema is fully Pydantic-validated at YAML load — no hand-rolled
dispatch / parse / coerce layer. The resolver only walks typed objects.
"""

from __future__ import annotations

import functools
from collections.abc import Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml

from litmus.execution._state import (
    get_active_profile,
    get_active_spec_context,
    get_active_vector_params,
)
from litmus.models.test_config import (
    Limit,
    MeasurementLimitConfig,
    SidecarConfig,
    TestEntry,
)
from litmus.store import expand_ranges

if TYPE_CHECKING:
    from litmus.models.project import ProfileConfig


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


def _merge_marker_fields_into(target: TestEntry, src: TestEntry) -> None:
    """In-place merge ``src``'s Litmus-marker fields into ``target`` (last-wins).

    * ``limits`` / ``prompts`` — dict-key update (later overrides earlier).
    * ``sweeps`` / ``mocks`` — list extend (file-level appears outer; later
      entries with the same target overwrite during marker resolution).
    * ``specs`` — last-wins replacement (single iteration scope in v1).
    * ``connections`` / ``retry`` — last-wins replacement (singletons).
    """
    target.limits.update(src.limits)
    target.sweeps.extend(src.sweeps)
    target.mocks.extend(src.mocks)
    if src.specs:
        target.specs = list(src.specs)
    if src.connections is not None:
        target.connections = src.connections.model_copy(deep=True)
    if src.retry is not None:
        target.retry = src.retry.model_copy(deep=True)
    target.prompts.update(src.prompts)


def _merge_runner_into(target: dict[str, Any], src: Mapping[str, Any]) -> None:
    """In-place merge an opaque ``runner:`` block; ``markers`` lists concat, others last-wins."""
    for key, value in src.items():
        if key == "markers" and isinstance(value, list):
            existing = target.get("markers")
            if isinstance(existing, list):
                target["markers"] = existing + list(value)
            else:
                target["markers"] = list(value)
        else:
            target[key] = value


def _merge_entry_into(target: TestEntry, src: TestEntry) -> None:
    """Merge marker fields + runner from ``src`` into ``target`` (last-wins).

    Does not recurse into ``src.tests`` — that walk is the caller's
    responsibility (see :func:`merged_test_entry` and
    :func:`profiles._merge_test_entries`).
    """
    _merge_marker_fields_into(target, src)
    _merge_runner_into(target.runner, src.runner)


def merged_test_entry(
    root: SidecarConfig | ProfileConfig | TestEntry | None,
    cls_name: str | None,
    func_name: str | None,
) -> TestEntry:
    """Walk a sidecar-shaped tree to build a merged :class:`TestEntry` for one test.

    Cascade order (each step extends the prior with last-wins
    semantics): root → class branch → leaf. Leaf lookup precedence
    (most → least specific):

    1. nested ``tests[Class].tests[method]`` (preferred form)
    2. dotted shorthand ``tests["Class.method"]`` at the root
    3. bare ``tests[method]`` shorthand at the root

    The first match wins; class-branch fields still apply when the leaf
    came from a dotted or bare shorthand. Accepts any of
    :class:`SidecarConfig`, :class:`ProfileConfig`, or :class:`TestEntry`
    — they share the same flat shape.
    """
    out = TestEntry()
    if root is None:
        return out
    _merge_entry_into(out, root)
    if cls_name is not None:
        class_branch = root.tests.get(cls_name)
        if class_branch is not None:
            _merge_entry_into(out, class_branch)
            if func_name is not None:
                method_leaf = class_branch.tests.get(func_name)
                if method_leaf is not None:
                    _merge_entry_into(out, method_leaf)
                    return out
    if func_name is None:
        return out
    if cls_name is not None:
        dotted_leaf = root.tests.get(f"{cls_name}.{func_name}")
        if dotted_leaf is not None:
            _merge_entry_into(out, dotted_leaf)
            return out
    bare_leaf = root.tests.get(func_name)
    if bare_leaf is not None:
        _merge_entry_into(out, bare_leaf)
    return out


def _resolve_single(
    cfg: MeasurementLimitConfig,
    *,
    spec_ctx: Any,
    params: Mapping[str, Any],
    guardband_pct: float,
    test_char: str | None,
) -> Limit | None:
    """Resolve one :class:`MeasurementLimitConfig` (no band recursion) to a concrete :class:`Limit`.

    Dispatches on the policy fields the model carries:

    * ``characteristic:`` (or fall back to the test-level binding) +
      ``tolerance_pct`` / ``tolerance_abs`` → derive a band from the
      product characteristic's nominal at the active vector params.
    * ``characteristic:`` alone (no tolerance) → look up the
      characteristic's spec band on the active spec context, applying
      ``guardband_pct``.
    * Direct ``low`` / ``high`` / ``nominal`` → return as-is.
    * Anything else (no policy declared) → ``None`` so the measurement
      records unchecked (characterization mode).
    """
    from litmus.execution.limits import _apply_guardband
    from litmus.models.enums import Comparator
    from litmus.models.test_config import Limit as LimitModel

    char_id = cfg.characteristic or test_char
    if char_id is not None and cfg.tolerance_pct is None and cfg.tolerance_abs is None:
        # Characteristic-only path: fetch the spec band straight off the
        # context (used to be the ``ref:`` branch). guardband_pct still
        # applies via the existing context method.
        if spec_ctx is None:
            return None
        try:
            return spec_ctx.get_limit(char_id, guardband_pct=guardband_pct, **dict(params))
        except (KeyError, ValueError):
            return None

    if char_id is not None and (cfg.tolerance_pct is not None or cfg.tolerance_abs is not None):
        if spec_ctx is None:
            return None
        char = spec_ctx.product.characteristics.get(char_id)
        if char is None:
            return None
        band = char.get_spec_at(dict(params))
        if band is None or not isinstance(band.value, (int, float)):
            return None
        nominal = float(band.value)
        if cfg.tolerance_pct is not None:
            delta = abs(nominal) * cfg.tolerance_pct / 100.0
        else:
            assert cfg.tolerance_abs is not None
            delta = float(cfg.tolerance_abs)
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

    return cfg.to_limit()


def resolve_limit(
    cfg: MeasurementLimitConfig,
    *,
    test_char: str | None = None,
) -> Limit | None:
    """Resolve one measurement's limit against active state.

    Walks ``cfg.bands`` in order; the first band whose ``when:`` matches
    the active vector params resolves. **If no band matches (or no
    bands are declared) the parent config itself is the catch-all** —
    its sibling-to-``bands:`` fields define the fallback limit.

    The catch-all is by design of :class:`MeasurementLimitConfig` —
    every band is itself a :class:`MeasurementLimitConfig` with its
    own ``when:``, and the parent is just an empty-``when:`` band
    sitting outside the list.

    Returns ``None`` when the resolved policy can't produce a Limit
    (missing spec context, characterization mode, etc.) so the
    measurement records unchecked instead of failing.
    """
    from litmus.models.capability import SpecBand, band_matches

    spec_ctx = get_active_spec_context()
    profile = get_active_profile()
    guardband_pct = float(getattr(profile, "guardband_pct", 0.0) or 0.0) if profile else 0.0
    params = get_active_vector_params()

    for band in cfg.bands:
        probe = (
            SpecBand.model_validate({"when": dict(band.when)}) if band.when else SpecBand(when={})
        )
        if band_matches(probe, dict(params)):
            return _resolve_single(
                band,
                spec_ctx=spec_ctx,
                params=params,
                guardband_pct=guardband_pct,
                test_char=test_char,
            )

    # No band matched — siblings to bands: are the catch-all.
    return _resolve_single(
        cfg,
        spec_ctx=spec_ctx,
        params=params,
        guardband_pct=guardband_pct,
        test_char=test_char,
    )

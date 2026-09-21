"""Promote comparison-shaped ``assert`` statements into named measurements.

GitHub issue #79: idiomatic pytest tests write ``assert lo <= x <= hi,
"name (unit)"`` and get nothing but pass/fail out of it — the value,
the bounds, and the name are all lost. This module recognizes a fixed
set of comparison shapes at import time, rewrites them into a call to
one of the ``record_*`` helpers below, and routes the captured
value/limit through the *existing* ``verify()`` pipeline
(:func:`testerkit.execution.verify._perform_verify`) so a promoted
assert produces exactly the measurement row a hand-written
``verify(name, value, limit=Limit(...))`` would — including step/vector
judgment-intent tracking (``run_scope.measure`` already calls
``pytest_plugin.hooks.mark_step_judgment_intent`` whenever a recorded
measurement carries a limit; a promoted assert gets this for free
because it's the same code path). No parallel store.

**Mechanism** — why an AST transform, and why it hooks
``_pytest.assertion.rewrite.rewrite_asserts`` specifically:

Pytest's own assertion rewriter is failure-oriented: it reconstructs
operand values for a nice diff, but only when the assert *fails*. The
issue's refinement (see the GH comment) asks for the operand value AND
the bounds on *both* pass and fail, dynamically — the numbers may be
variables or computed, not just literals. The only way to get that is
to route the comparison through our own code on every execution
instead of relying on the bare ``assert`` opcode. So at collection
time we AST-transform each recognized ``ast.Assert`` into a plain
function call (an ``ast.Expr``, not an ``ast.Assert``) that evaluates
the operands once and defers to a ``record_*`` helper.

That transform has to run *before* pytest's own rewriter sees the
module, because after pytest rewrites an ``ast.Assert`` node into its
instrumented form, it is no longer a bare ``Compare`` we can pattern-match.
``_pytest.assertion.rewrite.rewrite_asserts(tree, source, module_path,
config)`` is the exact function pytest calls (from ``_rewrite_test``)
to mutate the freshly-parsed module tree in place before compiling it.
We monkeypatch that module-level name so our transform runs first, then
delegate to the original for whatever ``ast.Assert`` nodes remain
(every non-promoted assert in the file keeps pytest's full rewritten
diff, unaffected).

**Design decisions worth flagging** (see the issue-#79 PR/report for
the full writeup):

1. **Disabled = a true no-op.** The transform only rewrites an assert
   when promotion is ENABLED for that function — project config
   ``assert_measurements: true`` OR a statically-visible
   ``@pytest.mark.testerkit_assert_measurements`` (decorator, or a
   module-/class-level ``pytestmark``). When neither applies, the
   ``ast.Assert`` node is left completely untouched: pytest's own
   rewriter handles it exactly as it would without this plugin at all
   (same failure diff, same short-circuit, same "message only
   evaluated on failure" semantics).
2. **Cache correctness without a runtime enabled-check.** Pytest
   bytecode-caches the rewritten module per source file
   (``__pycache__/*.pyc``), keyed by a tag string
   (``_pytest.assertion.rewrite.PYTEST_TAG``) baked into the cache
   filename — NOT by ``testerkit.yaml`` content. If the transform's
   enabled/disabled OUTCOME depended on live config but the cache key
   didn't, flipping ``assert_measurements:`` between runs could
   silently reuse a `.pyc` compiled under the old setting until the
   source file's mtime changed — the same staleness class already
   documented on ``pytest_load_initial_conftests`` for
   ``enable_assertion_pass_hook``. Fix: :func:`install_assert_measurement_hook`
   mixes the resolved enabled boolean straight into
   ``PYTEST_TAG``/``PYC_TAIL`` (see :func:`_sync_rewriter_cache_tag`),
   the same mechanism ``hooks._enable_hook_in_rewriter_cache_key``
   already uses for the always-on assertion-pass hook. Toggling the
   config therefore lands on a *different* cache filename outright —
   there is no stale-reuse window to reason about.
3. **Marker gate is resolved statically** (from the decorator / a
   module- or class-level ``pytestmark`` literally present in the
   source), which is safe to cache on its own — it's a pure function
   of file content, so ordinary mtime-based pyc invalidation covers a
   marker being added/removed. Only the project-wide default needs the
   cache-tag treatment in point 2, because it comes from an external
   YAML file the pyc cache knows nothing about.
4. **Pattern set is deliberately narrow** (see ``_recognize_assert``):
   ``lo <= x <= hi`` / ``lo < x < hi`` (and the two mixed-inclusivity
   chains — GELE / GELT / GTLE / GTLT), ``x >= lo`` / ``x <= hi`` /
   ``x > lo`` / ``x < hi`` (and their reversed spelling, e.g.
   ``lo <= x`` — see point 5), ``x == n`` / ``x != n`` (numeric EQ/NE),
   and ``x == pytest.approx(n, ...)`` (a GELE band: low/high =
   ``expected ∓ tolerance``). String equality, membership
   (``in``/``not in``), and boolean (``is True`` / ``is False``)
   asserts are explicitly NOT promoted in this first cut (flagged as
   TODOs in the GH issue) — they fall through untouched to pytest's
   normal rewriter. The comparator on the recorded ``Limit`` always
   matches the actual operator (never hardcoded to GELE except the
   approx band, where GELE is the correct shape for a symmetric
   tolerance).
5. **Value vs. bound identification is a hybrid rule, not pure
   position:**
   * **Chain** (``lo <= x <= hi``, 2 ops / 3 operands): the MIDDLE
     operand is always the value; the two outer operands are the
     bounds. Unambiguous by shape.
   * **Two-term** (1 op, incl. ``==``/``!=``): if exactly one operand
     is a literal (``ast.Constant``), that's the bound/nominal and the
     OTHER operand is the value — so both ``x >= 20`` and ``20 <= x``
     recognize ``x`` as the value (the comparison sense is mirrored for
     the reversed spelling; see ``_MIRROR_OP`` — equality doesn't need
     this, ``==``/``!=`` are symmetric). If both operands are
     non-constant (``x >= threshold``), we can't tell which is "the
     measurement" from shape alone, so we fall back to LEFT = value
     (matches every example in the issue). If BOTH operands are
     literals, there's nothing dynamic to measure — not promoted.
6. **Non-numeric operands degrade gracefully, not silently wrong**:
   ``Limit`` rejects non-numeric/bool low/high/nominal
   (``models/test_config.py``). The transform can't tell a numeric
   comparison from a string one statically (``x == n`` matches the
   same AST shape whether ``n`` is ``5`` or ``"5V"``), so this is a
   RUNTIME check: if any promoted-shape operand turns out not to be
   plain ``int``/``float`` at execution time, no measurement is
   recorded — we fall back to a plain comparison + ``AssertionError``
   equivalent to what the un-promoted ``assert`` would have raised.
   This is how string-equality asserts stay non-promoted in practice
   even though ``x == n`` is a recognized shape.
7. **A promoted chain evaluates all operands eagerly** — ``lo <= x <=
   hi`` becomes a 3-argument call, so Python evaluates ``lo``, ``x``,
   AND ``hi`` regardless of whether the first comparison already
   failed. Verified this is NOT a regression relative to ordinary
   pytest: raw, un-rewritten Python short-circuits a chained
   comparison, but pytest's OWN assertion rewriter already doesn't —
   ``_pytest.assertion.rewrite.AssertionRewriter.visit_Compare``
   evaluates every comparator up front (to build its diff) before
   combining the results with ``and``, for EVERY chained-comparison
   assert pytest rewrites, promoted or not. So a promoted chain's eager
   evaluation matches what a plain ``assert lo <= x <= hi()`` already
   does under pytest with no plugin involved at all; only comparing
   against bare CPython bytecode outside pytest would show a
   difference, and that comparison was never available to begin with
   once pytest's rewriter is in the loop.
8. **The message expression is evaluated unconditionally for a
   promoted assert** (even on pass) because the name is needed either
   way. A bare ``assert test, msg`` only evaluates ``msg`` on failure;
   a promoted one always does. A disabled/non-promoted assert is
   unaffected (point 1).

See ``tests/test_execution/test_assert_measurements.py`` for the
behavior this produces end to end.
"""

from __future__ import annotations

import ast
import re
from typing import Any, NoReturn

_MARKER_NAME = "testerkit_assert_measurements"
_RECORDER_MODULE = "testerkit.pytest_plugin.assert_measurements"
_MODULE_ALIAS = "__testerkit_assert_measurements__"

_UNIT_RE = re.compile(r"^(?P<name>.*?)\s*\((?P<unit>[^()]+)\)\s*$")


# ---------------------------------------------------------------------------
# Session-scoped enabled state — the project-wide default only (the marker
# override is resolved statically at transform time, see point 3 above).
# Set once per session by ``install_assert_measurement_hook``, read by the
# transform at module-rewrite time (during collection, always after
# ``pytest_load_initial_conftests`` has run).
# ---------------------------------------------------------------------------

_default_enabled = False


def set_project_default_enabled(value: bool) -> None:
    """Set the project-wide default for assert-measurement promotion.

    Called from :func:`install_assert_measurement_hook` with
    ``ProjectConfig.assert_measurements``.
    """
    global _default_enabled
    _default_enabled = bool(value)


def _project_default_enabled() -> bool:
    return _default_enabled


# ---------------------------------------------------------------------------
# Message parsing — assert message -> (name, unit). Trailing "(unit)"
# supplies the unit; the text before it (trimmed) is the measurement name.
# ---------------------------------------------------------------------------


def parse_assert_message(message: Any) -> tuple[str, str | None]:
    """Split an assert message into ``(name, unit)``.

    ``"Case temperature (°C)"`` -> ``("Case temperature", "°C")``.
    ``"Case temperature"`` -> ``("Case temperature", None)``.
    Non-string messages are stringified (rare: ``assert test, some_obj``).
    """
    text = message if isinstance(message, str) else str(message)
    text = text.strip()
    m = _UNIT_RE.match(text)
    if m:
        return m.group("name").strip(), m.group("unit").strip()
    return text, None


# ---------------------------------------------------------------------------
# Runtime recorders — one per promoted call shape. Only reached for an
# assert the transform already decided to promote (see point 1 above);
# there is no "check if enabled" here, only a numeric-operand sanity
# fallback (point 6).
# ---------------------------------------------------------------------------


def _is_plain_numeric(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _manual_bound_check(
    value: Any, low: Any, high: Any, *, low_inclusive: bool, high_inclusive: bool
) -> bool:
    """Native-operator comparison — works for numbers, strings, anything comparable.

    Used only as the non-numeric-operand fallback (point 6) so a
    promoted assert whose operands turn out not to be measurement
    material still behaves exactly like the ``assert`` it replaced.
    """
    if low is not None:
        ok = value >= low if low_inclusive else value > low
        if not ok:
            return False
    if high is not None:
        ok = value <= high if high_inclusive else value < high
        if not ok:
            return False
    return True


def _assert_style_message(
    message: Any, compare_source: str | None, value_source: str | None, value: Any
) -> str:
    """Render a failure message that reads like the original ``assert``.

    Used for BOTH the non-numeric fallback (a plain ``AssertionError``)
    and the numeric FAIL path (re-raising ``LimitFailure`` with this
    message instead of its default value-vs-limit format — see
    :func:`record_range` / :func:`record_equality` / :func:`record_approx`).

    ``compare_source`` / ``value_source`` come from ``ast.unparse`` at
    transform time (the whole comparison, and just the value operand).
    We substitute the value operand's own source text with its runtime
    repr inside the comparison and add a single ``where`` line for it —
    honest scope (module docstring): this reproduces the TOP-LEVEL
    comparison + one ``where``, not pytest's arbitrarily-deep nested
    ``where`` chain. Bounds that are literals or other variables keep
    their original source spelling (only the measured value is
    substituted), e.g.::

        Case temperature (°C)
        assert 20 <= 91.0 <= 80
         +  where 91.0 = temp

    Falls back to a bare ``"{message}"`` if the transform didn't supply
    ``compare_source``/``value_source`` (defensive — always populated
    in practice) or the value's source text isn't found verbatim in the
    comparison source (rare — a value operand whose ``ast.unparse``
    rendering differs standalone vs. embedded in the comparison).
    """
    lines = [str(message)]
    if compare_source and value_source and value_source in compare_source:
        rendered = compare_source.replace(value_source, repr(value), 1)
        lines.append(f"assert {rendered}")
        lines.append(f" +  where {value!r} = {value_source}")
    return "\n".join(lines)


def _reraise_assert_style(
    original: Any,
    message: Any,
    compare_source: str | None,
    value_source: str | None,
    value: Any,
) -> NoReturn:
    """Re-raise a caught ``LimitFailure`` with an assert-style message.

    Scoped entirely to the promoted-assert recorders — ``verify.py`` /
    ``LimitFailure`` itself are untouched (no ``message=`` parameter
    there); a hand-written ``verify(...)`` failure keeps its ordinary
    value-vs-limit wording. ``_perform_verify`` already ran (and
    already recorded the FAILED measurement via ``run_scope.measure``)
    by the time this is called — we build a SECOND ``LimitFailure``
    with identical structured fields (``name``/``value``/``limit``/
    ``uut_pin``/``spec_ref``) via its normal constructor, then override
    just its rendered message (``BaseException.args``) before raising
    it chained (``from original``) — no second measurement row, only
    the exception's wording differs from what ``verify()`` would show.
    """
    from testerkit.execution.verify import LimitFailure

    reraised = LimitFailure(
        name=original.name,
        value=original.value,
        limit=original.limit,
        uut_pin=original.uut_pin,
        spec_ref=original.spec_ref,
    )
    reraised.args = (_assert_style_message(message, compare_source, value_source, value),)
    raise reraised from original


def record_range(
    value: Any,
    low: Any,
    high: Any,
    *,
    low_inclusive: bool,
    high_inclusive: bool,
    message: Any,
    compare_source: str | None = None,
    value_source: str | None = None,
) -> None:
    """Runtime recorder for ``lo <= x <= hi`` / ``x >= lo`` / ``x <= hi`` / ``x > lo`` / ``x < hi``.

    Only ever called for an assert the transform already decided to
    promote. Always evaluates the full comparison (both bounds, on
    pass and fail per the issue's refinement). When all participating
    operands are numeric, routes the value + a ``Limit`` built from the
    bounds through ``testerkit.execution.verify._perform_verify`` — the
    same pipeline a hand-written ``verify(name, value,
    limit=Limit(...))`` uses, so it records a measurement row (with
    judgment-intent tracking) on either outcome. On FAIL, the resulting
    ``LimitFailure`` is re-raised with an assert-style message (see
    :func:`_assert_style_message`) chained via ``from`` — the row
    ``_perform_verify`` already recorded is not touched again, only the
    exception's wording changes. A non-numeric operand falls back to
    plain assert semantics (point 6): no row, plain ``AssertionError``
    on failure.
    """
    operands = [v for v in (value, low, high) if v is not None]
    if not all(_is_plain_numeric(v) for v in operands):
        if not _manual_bound_check(
            value, low, high, low_inclusive=low_inclusive, high_inclusive=high_inclusive
        ):
            raise AssertionError(
                _assert_style_message(message, compare_source, value_source, value)
            )
        return

    from testerkit.execution.verify import LimitFailure, _perform_verify
    from testerkit.models.enums import Comparator
    from testerkit.models.test_config import Limit

    name, unit = parse_assert_message(message)

    if low is not None and high is not None:
        comparator = {
            (True, True): Comparator.GELE,
            (True, False): Comparator.GELT,
            (False, True): Comparator.GTLE,
            (False, False): Comparator.GTLT,
        }[(low_inclusive, high_inclusive)]
    elif low is not None:
        comparator = Comparator.GE if low_inclusive else Comparator.GT
    else:
        comparator = Comparator.LE if high_inclusive else Comparator.LT

    limit = Limit(
        low=float(low) if low is not None else None,
        high=float(high) if high is not None else None,
        unit=unit or "",
        comparator=comparator,
    )
    try:
        _perform_verify(name, value, limit=limit)
    except LimitFailure as original:
        _reraise_assert_style(original, message, compare_source, value_source, value)


def record_approx(
    value: Any,
    approx_obj: Any,
    *,
    message: Any,
    compare_source: str | None = None,
    value_source: str | None = None,
) -> None:
    """Runtime recorder for ``x == pytest.approx(n, rel=..., abs=...)``.

    Only ever called for an assert the transform already decided to
    promote. Only ``pytest.approx`` of a single scalar (``ApproxScalar``
    — exposes ``.expected`` / ``.tolerance``) promotes to a measurement;
    ``pytest.approx`` of a sequence/mapping/etc. isn't scalar Limit
    material and falls back to plain assert semantics. On FAIL, the
    ``LimitFailure`` is re-raised with an assert-style message — see
    :func:`record_range`.
    """
    expected = getattr(approx_obj, "expected", None)
    tolerance: float | None = None
    if _is_plain_numeric(value) and _is_plain_numeric(expected):
        try:
            tolerance = float(approx_obj.tolerance)
        except (AttributeError, ValueError, TypeError):
            tolerance = None

    if tolerance is None:
        if not (value == approx_obj):
            raise AssertionError(
                _assert_style_message(message, compare_source, value_source, value)
            )
        return

    from testerkit.execution.verify import LimitFailure, _perform_verify
    from testerkit.models.enums import Comparator
    from testerkit.models.test_config import Limit

    assert expected is not None  # narrowed by _is_plain_numeric(expected) above
    name, unit = parse_assert_message(message)
    expected_f = float(expected)
    limit = Limit(
        low=expected_f - tolerance,
        high=expected_f + tolerance,
        nominal=expected_f,
        unit=unit or "",
        comparator=Comparator.GELE,
    )
    try:
        _perform_verify(name, value, limit=limit)
    except LimitFailure as original:
        _reraise_assert_style(original, message, compare_source, value_source, value)


def record_equality(
    value: Any,
    nominal: Any,
    *,
    negate: bool,
    message: Any,
    compare_source: str | None = None,
    value_source: str | None = None,
) -> None:
    """Runtime recorder for ``x == n`` / ``x != n`` (numeric ``nominal``, not ``pytest.approx``).

    ``Limit`` rejects non-numeric/bool nominals, so this only promotes
    when BOTH ``value`` and ``nominal`` are plain numeric — string
    equality, membership, and boolean asserts structurally never reach
    here (the transform doesn't recognize those shapes at all, per the
    GH issue's own phasing), but a bare ``x == n`` where ``x`` turns
    out to be a string at runtime (nothing in the AST tells us that
    statically) degrades to a plain equality check + ``AssertionError``
    instead of recording a measurement — same non-numeric fallback
    pattern as :func:`record_range` (module docstring, point 6). On
    FAIL, the ``LimitFailure`` is re-raised with an assert-style
    message — see :func:`record_range`.
    """
    if not (_is_plain_numeric(value) and _is_plain_numeric(nominal)):
        ok = (value != nominal) if negate else (value == nominal)
        if not ok:
            raise AssertionError(
                _assert_style_message(message, compare_source, value_source, value)
            )
        return

    from testerkit.execution.verify import LimitFailure, _perform_verify
    from testerkit.models.enums import Comparator
    from testerkit.models.test_config import Limit

    name, unit = parse_assert_message(message)
    comparator = Comparator.NE if negate else Comparator.EQ
    limit = Limit(nominal=float(nominal), unit=unit or "", comparator=comparator)
    try:
        _perform_verify(name, value, limit=limit)
    except LimitFailure as original:
        _reraise_assert_style(original, message, compare_source, value_source, value)


# ---------------------------------------------------------------------------
# AST transform
# ---------------------------------------------------------------------------

# Single-operator comparisons, value on the LEFT: op type -> (bound side, inclusive?)
_BOUND_OPS: dict[type, tuple[str, bool]] = {
    ast.GtE: ("low", True),
    ast.Gt: ("low", False),
    ast.LtE: ("high", True),
    ast.Lt: ("high", False),
}

# Mirror an op when the value turns out to be on the RIGHT (e.g. ``20 <= x``):
# rewriting ``left OP right`` as ``right OP' left`` swaps the comparison sense.
_MIRROR_OP: dict[type, type] = {
    ast.Lt: ast.Gt,
    ast.LtE: ast.GtE,
    ast.Gt: ast.Lt,
    ast.GtE: ast.LtE,
}


def _identify_value_and_bound(
    left: ast.expr, right: ast.expr
) -> tuple[ast.expr, ast.expr, bool] | None:
    """Hybrid value/bound identification (module docstring, point 5).

    Returns ``(value_expr, bound_expr, swapped)``, or ``None`` when both
    operands are literals (nothing dynamic to measure). ``swapped`` is
    True when the bound was on the LEFT (value on the right) — a
    range-comparison caller must mirror the operator's sense in that
    case (see ``_MIRROR_OP``); an equality caller can ignore it
    (``==``/``!=`` are symmetric).
    """
    left_is_const = isinstance(left, ast.Constant)
    right_is_const = isinstance(right, ast.Constant)
    if left_is_const and right_is_const:
        return None
    if left_is_const and not right_is_const:
        return right, left, True
    return left, right, False


def _is_approx_call(node: ast.expr) -> bool:
    """True if ``node`` is a call to ``pytest.approx`` / a bare ``approx``."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    if isinstance(func, ast.Attribute) and func.attr == "approx":
        return True
    return isinstance(func, ast.Name) and func.id == "approx"


def _marker_expr_matches(expr: ast.expr) -> bool:
    target = expr.func if isinstance(expr, ast.Call) else expr
    return isinstance(target, ast.Attribute) and target.attr == _MARKER_NAME


def _marker_present(decorator_list: list[ast.expr]) -> bool:
    return any(_marker_expr_matches(dec) for dec in decorator_list)


def _pytestmark_present(body: list[ast.stmt]) -> bool:
    """Scan a module/class body for ``pytestmark = pytest.mark.<marker>`` (or a list)."""
    for stmt in body:
        if not (
            isinstance(stmt, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "pytestmark" for t in stmt.targets)
        ):
            continue
        values = stmt.value.elts if isinstance(stmt.value, (ast.List, ast.Tuple)) else [stmt.value]
        if any(_marker_expr_matches(v) for v in values):
            return True
    return False


def _source_keywords(test: ast.Compare, value_expr: ast.expr) -> list[ast.keyword]:
    """Best-effort ``compare_source``/``value_source`` keywords, for assert-style FAIL messages.

    ``ast.unparse`` of the whole comparison and of just the value
    operand — threaded into the recorder call so a promoted assert's
    failure can read like the original ``assert`` (see
    ``_assert_style_message``). Any ``ast.unparse`` failure (should not
    happen for a tree we just recognized — defensive only) degrades to
    omitting these kwargs, in which case the recorder falls back to a
    bare message.
    """
    try:
        compare_source = ast.unparse(test)
        value_source = ast.unparse(value_expr)
    except Exception:
        return []
    return [
        ast.keyword(arg="compare_source", value=ast.Constant(value=compare_source)),
        ast.keyword(arg="value_source", value=ast.Constant(value=value_source)),
    ]


def _build_call(
    func_attr: str, args: list[ast.expr], keywords: list[ast.keyword], template: ast.AST
) -> ast.Expr:
    call = ast.Call(
        func=ast.Attribute(
            value=ast.Name(id=_MODULE_ALIAS, ctx=ast.Load()),
            attr=func_attr,
            ctx=ast.Load(),
        ),
        args=args,
        keywords=keywords,
    )
    expr = ast.Expr(value=call)
    ast.copy_location(call.func, template)
    ast.copy_location(call.func.value, template)  # type: ignore[attr-defined]
    ast.copy_location(call, template)
    ast.copy_location(expr, template)
    ast.fix_missing_locations(expr)
    return expr


def _recognize_assert(node: ast.Assert) -> ast.Expr | None:
    """Return the replacement ``ast.Expr`` for a recognized assert, or ``None``.

    ``None`` means "leave this assert exactly as it is" — either
    there's no message (per the issue: no message -> don't promote) or
    the comparison shape isn't one of the patterns this first cut
    supports (see module docstring, point 4). Only called for asserts
    in a function the caller has already decided to promote (point 1)
    — this function is pattern recognition only, not the enabled gate.
    """
    if node.msg is None:
        return None
    test = node.test
    if not isinstance(test, ast.Compare):
        return None
    msg = node.msg

    # 3-term chain: lo <= x <= hi / lo < x < hi / mixed inclusivity.
    # The MIDDLE operand is always the value (module docstring, point 5).
    if len(test.ops) == 2 and all(isinstance(op, (ast.Lt, ast.LtE)) for op in test.ops):
        low_expr, mid_expr, high_expr = test.left, test.comparators[0], test.comparators[1]
        return _build_call(
            "record_range",
            args=[mid_expr, low_expr, high_expr],
            keywords=[
                ast.keyword(
                    arg="low_inclusive", value=ast.Constant(value=isinstance(test.ops[0], ast.LtE))
                ),
                ast.keyword(
                    arg="high_inclusive", value=ast.Constant(value=isinstance(test.ops[1], ast.LtE))
                ),
                ast.keyword(arg="message", value=msg),
                *_source_keywords(test, mid_expr),
            ],
            template=node,
        )

    if len(test.ops) != 1:
        return None

    op = test.ops[0]
    left, right = test.left, test.comparators[0]

    if isinstance(op, ast.Eq) and _is_approx_call(right):
        return _build_call(
            "record_approx",
            args=[left, right],
            keywords=[ast.keyword(arg="message", value=msg), *_source_keywords(test, left)],
            template=node,
        )

    if isinstance(op, (ast.Eq, ast.NotEq)):
        identified = _identify_value_and_bound(left, right)
        if identified is None:
            return None
        value_expr, nominal_expr, _swapped = identified
        return _build_call(
            "record_equality",
            args=[value_expr, nominal_expr],
            keywords=[
                ast.keyword(arg="negate", value=ast.Constant(value=isinstance(op, ast.NotEq))),
                ast.keyword(arg="message", value=msg),
                *_source_keywords(test, value_expr),
            ],
            template=node,
        )

    # Hybrid value/bound identification (module docstring, point 5):
    # a lone literal operand is the bound, the other side is the value
    # (handles both ``x >= 20`` and ``20 <= x``); two literals -> nothing
    # dynamic to measure; two non-literals -> fall back to left=value.
    identified = _identify_value_and_bound(left, right)
    if identified is None:
        return None
    value_expr, bound_expr, swapped = identified
    lookup_op = _MIRROR_OP.get(type(op)) if swapped else type(op)

    op_kind = _BOUND_OPS.get(lookup_op) if lookup_op is not None else None
    if op_kind is None:
        return None
    bound_kind, inclusive = op_kind
    low_expr: ast.expr = bound_expr if bound_kind == "low" else ast.Constant(value=None)
    high_expr: ast.expr = bound_expr if bound_kind == "high" else ast.Constant(value=None)
    return _build_call(
        "record_range",
        args=[value_expr, low_expr, high_expr],
        keywords=[
            ast.keyword(
                arg="low_inclusive",
                value=ast.Constant(value=inclusive if bound_kind == "low" else True),
            ),
            ast.keyword(
                arg="high_inclusive",
                value=ast.Constant(value=inclusive if bound_kind == "high" else True),
            ),
            ast.keyword(arg="message", value=msg),
            *_source_keywords(test, value_expr),
        ],
        template=node,
    )


class _AssertPromoter(ast.NodeTransformer):
    """Replaces recognized ``Assert`` nodes within one ``test_*`` function.

    Only constructed for a function the caller has already decided is
    enabled (point 1) — every ``ast.Assert`` it visits is a candidate,
    gated only by shape recognition (:func:`_recognize_assert`), not by
    an enabled check. Does not recurse into nested
    ``def``/``async def``/``lambda`` — asserts in a helper closure
    defined *inside* a test function are left untouched, only the test
    function's own statement tree (including nested
    ``if``/``for``/``with``/``try`` blocks) is promoted.
    """

    def __init__(self) -> None:
        self.promoted = False

    def visit_Assert(self, node: ast.Assert) -> ast.stmt:
        replacement = _recognize_assert(node)
        if replacement is None:
            return node
        self.promoted = True
        return replacement

    def visit_FunctionDef(self, node: ast.FunctionDef) -> ast.stmt:
        return node

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> ast.stmt:
        return node

    def visit_Lambda(self, node: ast.Lambda) -> ast.expr:
        return node


def _promote_asserts_in_function(fn: ast.FunctionDef | ast.AsyncFunctionDef) -> bool:
    promoter = _AssertPromoter()
    promoter.generic_visit(fn)
    return promoter.promoted


def _leading_prelude_end(body: list[ast.stmt]) -> int:
    """Index just past a module docstring + any leading ``__future__`` imports."""
    idx = 0
    if (
        body
        and isinstance(body[0], ast.Expr)
        and isinstance(body[0].value, ast.Constant)
        and isinstance(body[0].value.value, str)
    ):
        idx = 1
    while idx < len(body):
        stmt = body[idx]
        if not (isinstance(stmt, ast.ImportFrom) and stmt.module == "__future__"):
            break
        idx += 1
    return idx


def _inject_import(tree: ast.Module) -> None:
    idx = _leading_prelude_end(tree.body)
    import_stmt = ast.Import(names=[ast.alias(name=_RECORDER_MODULE, asname=_MODULE_ALIAS)])
    ast.copy_location(import_stmt, tree.body[0])
    ast.fix_missing_locations(import_stmt)
    tree.body.insert(idx, import_stmt)


def transform_module(tree: ast.Module, *, default_enabled: bool) -> bool:
    """Mutate ``tree`` in place, promoting recognized asserts in ENABLED ``test_*`` functions.

    A function is enabled when ``default_enabled`` (the project-wide
    ``assert_measurements:`` config, resolved once per session) is
    True, OR a ``testerkit_assert_measurements`` marker is statically
    visible on the function itself, its enclosing class's
    ``pytestmark``, or the module's ``pytestmark``. When NOT enabled,
    a function's asserts are left completely untouched (point 1 — true
    no-op).

    Recurses into module-level ``class`` bodies (one level — pytest
    doesn't support deeper test nesting either) so ``TestFoo.test_bar``
    methods are covered. Returns ``True`` if anything was promoted, so
    the caller knows whether to inject the recorder-module import.
    """
    module_marker = _pytestmark_present(tree.body)
    changed = False

    def walk(stmts: list[ast.stmt], scope_marker: bool) -> None:
        nonlocal changed
        for stmt in stmts:
            if isinstance(stmt, ast.ClassDef):
                class_marker = (
                    scope_marker
                    or _pytestmark_present(stmt.body)
                    or _marker_present(stmt.decorator_list)
                )
                walk(stmt.body, class_marker)
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)) and stmt.name.startswith(
                "test_"
            ):
                fn_marker = scope_marker or _marker_present(stmt.decorator_list)
                enabled = default_enabled or fn_marker
                if enabled and _promote_asserts_in_function(stmt):
                    changed = True

    walk(tree.body, module_marker)
    if changed:
        _inject_import(tree)
    return changed


# ---------------------------------------------------------------------------
# Hook installation
# ---------------------------------------------------------------------------

_ASSERT_MEASUREMENTS_ON_TAG = "testerkit-assertmeasure-on"
_ASSERT_MEASUREMENTS_OFF_TAG = "testerkit-assertmeasure-off"


def _sync_rewriter_cache_tag(rewrite_module: Any, enabled: bool) -> None:
    """Mix the resolved enabled state into pytest's rewriter pyc cache key.

    Same fix as ``pytest_plugin.hooks._enable_hook_in_rewriter_cache_key``,
    but conditional rather than always-on: the tag differs between an
    enabled and a disabled session, so a project flipping
    ``assert_measurements:`` lands on a distinct ``.pyc`` filename
    (``PYC_TAIL``) instead of risking a stale transform-or-not decision
    baked into a cached file compiled under the other setting. Also
    covers upgrading from a pre-feature TesterKit version — neither
    suffix existed in old tags, so any old cache misses regardless of
    which state this session resolves to. Idempotent AND re-syncable:
    safe to call every session even if a prior call in the same
    process set the opposite value (nested in-process pytest runs).
    """
    want = _ASSERT_MEASUREMENTS_ON_TAG if enabled else _ASSERT_MEASUREMENTS_OFF_TAG
    other = _ASSERT_MEASUREMENTS_OFF_TAG if enabled else _ASSERT_MEASUREMENTS_ON_TAG
    tag = rewrite_module.PYTEST_TAG
    if f"-{other}" in tag:
        tag = tag.replace(f"-{other}", "")
    if f"-{want}" not in tag:
        tag = f"{tag}-{want}"
    rewrite_module.PYTEST_TAG = tag
    rewrite_module.PYC_TAIL = "." + tag + rewrite_module.PYC_EXT


def install_assert_measurement_hook(enabled: bool) -> None:
    """Resolve the enabled state for this session and wire up the transform.

    ``enabled`` is the project-wide ``ProjectConfig.assert_measurements``
    default, resolved by the caller (``pytest_load_initial_conftests``,
    before any test module is read). This function:

    1. Publishes it via :func:`set_project_default_enabled` so the
       transform (point 1 — only promotes enabled functions) sees the
       current session's value.
    2. Syncs pytest's rewriter cache tag (see
       :func:`_sync_rewriter_cache_tag`) so the ``.pyc`` cache reflects
       it too.
    3. Monkeypatches ``_pytest.assertion.rewrite.rewrite_asserts`` ONCE
       (idempotent — checked via an attribute on the wrapped callable)
       so repeated plugin loads / nested in-process pytest sessions in
       the same interpreter don't stack wrappers. Steps 1-2 still run
       on every call (cheap), so a later session with a different
       ``enabled`` value is picked up correctly even though the
       monkeypatch itself is installed only the first time.
    """
    set_project_default_enabled(enabled)

    try:
        from _pytest.assertion import rewrite as _rewrite
    except ImportError:
        # Assertion rewriting isn't available (shouldn't happen under a
        # normal pytest install) -- promotion is simply unavailable;
        # never break collection over it.
        return

    _sync_rewriter_cache_tag(_rewrite, enabled)

    original = _rewrite.rewrite_asserts
    if getattr(original, "_testerkit_wrapped", False):
        return

    def _wrapped(
        mod: ast.Module, source: bytes, module_path: str | None = None, config: Any = None
    ) -> None:
        if module_path is not None:
            try:
                transform_module(mod, default_enabled=_project_default_enabled())
            except Exception:
                # A bug in our transform must never break normal pytest
                # collection -- fall through to vanilla rewriting of the
                # untouched tree.
                pass
        original(mod, source, module_path, config)

    _wrapped._testerkit_wrapped = True  # type: ignore[attr-defined]
    _rewrite.rewrite_asserts = _wrapped

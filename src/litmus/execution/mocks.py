"""Runner-neutral mock installation.

Given a merged dict of ``{target: MockEntry}`` and runner-specific
callbacks for fixture resolution + cleanup registration, install
each mock via :func:`unittest.mock.patch.object` and bind teardown
to the runner's per-test cleanup hook.

The runner provides:

* ``resolve_fixture(name) -> Any | None`` — return the fixture value,
  or ``None`` if it doesn't exist on this test (callback emits a
  warning and skips).
* ``register_cleanup(callable)`` — register a zero-arg cleanup that
  fires when the test completes (pytest: ``request.addfinalizer``;
  unittest: ``addCleanup``; OpenHTF: phase teardown).

This module owns the unittest.mock interaction so each runner can
share the same mock-lifecycle without copy-pasting.
"""

from __future__ import annotations

import warnings
from collections.abc import Callable
from typing import Any

from litmus.models.test_config import MockEntry


def install_mocks(
    by_target: dict[str, MockEntry],
    *,
    resolve_fixture: Callable[[str], Any],
    register_cleanup: Callable[[Callable[[], None]], None],
    fixture_lookup_error: type[Exception] = LookupError,
) -> None:
    """Install each mock and register its teardown.

    ``by_target`` is the de-duplicated merged map (later scopes
    overwrite earlier ones by target — caller does the cascade).
    Each :class:`MockEntry`'s ``target`` is ``"<fixture>.<attr>"``;
    the callback resolves the fixture, this function patches the
    attribute and binds cleanup. Missing fixtures emit a warning
    (caught via ``fixture_lookup_error`` from the runner's host) and
    skip — a typo in ``target`` shouldn't fail the run, just record.
    """
    from unittest.mock import patch as _patch

    for target, entry in by_target.items():
        fixture_name, _, attr = target.partition(".")
        try:
            fixture_value = resolve_fixture(fixture_name)
        except fixture_lookup_error:
            warnings.warn(
                f"litmus_mocks target {target!r}: fixture {fixture_name!r} not "
                "found on this test — mock skipped. Check the entry `target:` "
                "matches a fixture in the test's signature.",
                stacklevel=2,
            )
            continue
        patcher = _patch.object(fixture_value, attr, **entry.patch_kwargs())
        patcher.start()
        register_cleanup(patcher.stop)

"""Runner-neutral retry-policy translation.

Each runner translates a Litmus :class:`RetryPolicy` to its own retry
primitive — pytest maps to pytest-rerunfailures' ``@pytest.mark.flaky``,
OpenHTF maps to its phase-retry decorator, unittest maps to a custom
loop. The kwarg-mapping for ``flaky`` lives here so the pytest plugin
is a thin call site; the Pydantic model itself owns validation.

Inline decorators pass raw kwargs to ``@pytest.mark.litmus_retry(...)``
— the runner's plugin runs them through ``RetryPolicy.model_validate``
to apply the same constraints (``max_attempts >= 1``, ``delay >= 0``,
no extra fields) that sidecar / profile YAML gets at load.
"""

from __future__ import annotations

from typing import Any

from litmus.models.test_config import RetryPolicy


def retry_policy_to_flaky_kwargs(policy: RetryPolicy) -> dict[str, Any]:
    """Translate a :class:`RetryPolicy` into ``pytest-rerunfailures`` ``flaky`` kwargs.

    Mapping:

    * ``max_attempts=N`` → ``reruns=N-1`` — rerunfailures counts
      *additional* attempts after the first; Litmus counts *total*
      attempts.
    * ``delay=S`` → ``reruns_delay=S``.
    * ``on=[...]`` → ``only_rerun=[...]``.
    """
    flaky_kwargs: dict[str, Any] = {"reruns": max(0, policy.max_attempts - 1)}
    if policy.delay:
        flaky_kwargs["reruns_delay"] = policy.delay
    if policy.on is not None:
        flaky_kwargs["only_rerun"] = list(policy.on)
    return flaky_kwargs

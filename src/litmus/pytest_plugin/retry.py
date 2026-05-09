"""Runner-neutral retry-config translation.

Each runner translates a Litmus :class:`RetryConfig` to its own retry
primitive — pytest maps to pytest-rerunfailures' ``@pytest.mark.flaky``,
OpenHTF maps to its phase-retry decorator, unittest maps to a custom
loop. The kwarg-mapping for ``flaky`` lives here so the pytest plugin
is a thin call site; the Pydantic model itself owns validation.

Inline decorators pass raw kwargs to ``@pytest.mark.litmus_retry(...)``
— the runner's plugin runs them through ``RetryConfig.model_validate``
to apply the same constraints (``max_retries >= 0``, ``delay >= 0``,
no extra fields) that sidecar / profile YAML gets at load.
"""

from __future__ import annotations

from typing import Any

from litmus.models.test_config import RetryConfig


def retry_config_to_flaky_kwargs(config: RetryConfig) -> dict[str, Any]:
    """Translate a :class:`RetryConfig` into ``pytest-rerunfailures`` ``flaky`` kwargs.

    Mapping:

    * ``max_retries=N`` → ``reruns=N`` — both count *additional*
      executions beyond the original; the bases line up directly.
    * ``delay=S`` → ``reruns_delay=S``.
    * ``on=[...]`` → ``only_rerun=[...]``.
    """
    flaky_kwargs: dict[str, Any] = {"reruns": config.max_retries}
    if config.delay:
        flaky_kwargs["reruns_delay"] = config.delay
    if config.on is not None:
        flaky_kwargs["only_rerun"] = list(config.on)
    return flaky_kwargs

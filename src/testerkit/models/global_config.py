"""Global (per-machine) TesterKit configuration — ``<global-home>/config.yaml``.

Distinct from a project's ``testerkit.yaml``: this file holds machine-wide
defaults shared by every project checkout on the box, the same way
``machine_id`` and the credential store (``<global-home>/credentials``) are
per-machine rather than per-project. Currently holds only the cloud server
URL that ``testerkit connect`` persists on success, consulted by
``testerkit forward`` (and ``connect`` itself) when no ``--url`` /
``$TESTERKIT_URL`` / project ``testerkit.yaml`` ``server.url`` is set.
"""

from __future__ import annotations

from pydantic import BaseModel


class GlobalConfig(BaseModel):
    """Schema for the global ``config.yaml`` file."""

    model_config = {"extra": "forbid"}

    url: str | None = None

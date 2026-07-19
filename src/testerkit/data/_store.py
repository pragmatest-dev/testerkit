"""Shared lifecycle protocol for TesterKit data stores.

Every store that manages a background daemon or other releasable
resource satisfies this protocol structurally — no inheritance needed.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable


@runtime_checkable
class Store(Protocol):
    """Structural protocol for stores with an optional-close lifecycle.

    Types the shared lifecycle surface (``close`` / context-manager),
    not the per-store read/write methods, which diverge by design.
    """

    def close(self) -> None: ...

    def __enter__(self) -> Store: ...

    def __exit__(self, *_: object) -> None: ...

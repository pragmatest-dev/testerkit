"""Per-process identity — a uuid minted once per process.

Pairs with ``pid`` + ``hostname`` to form the producer's identity on the will
(``SessionStarted``). The uuid disambiguates a *restarted* producer that the OS
handed a recycled pid: same host+pid, different process_uuid ⇒ a new process,
not the original one the reaper is watching.
"""

from __future__ import annotations

from uuid import uuid4

_PROCESS_UUID = str(uuid4())


def process_uuid() -> str:
    """The current process's stable uuid (minted once at import)."""
    return _PROCESS_UUID

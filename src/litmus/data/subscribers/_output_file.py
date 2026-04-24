"""OutputFile descriptor — metadata about a file produced by a subscriber.

Transports use ``format`` to decide how to ship the file. The ``on_output``
callback receives an ``OutputFile`` after each successful write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class OutputFile:
    """Describes a file produced by a subscriber.

    Attributes:
        path: Absolute path to the written file.
        format: Format name (``"parquet"``, ``"csv"``, ``"stdf"``, etc.).
        run_id: Run ID associated with the file, if known.
    """

    path: Path
    format: str
    run_id: str | None = None

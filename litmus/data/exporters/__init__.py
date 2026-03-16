"""Output format subscribers and utilities.

All output formats are implemented as ``EventSubscriber`` instances.
The subscriber registry in ``litmus.data.subscribers`` is the single
source of truth for format name → class mappings.

``EventSubscriber`` protocol::

    class MySubscriber:
        format_name: str          # e.g. "csv"
        event_types: set[type]    # event classes to receive

        def __init__(
            self,
            output_dir: Path,
            *,
            on_output: Callable[[OutputFile], None] | None = None,
        ) -> None:
            # output_dir is the results root — subscriber owns its subfolder
            ...

        def open(self) -> None: ...
        def on_event(self, event) -> None: ...
        def close(self) -> None: ...

Constructor contract:

- ``output_dir`` is the **results root**.  Each subscriber creates its
  own subfolder (e.g. ``runs/``, ``exports/csv/``).
- ``on_output`` callback is called after each file is successfully
  written, with an ``OutputFile`` descriptor.  The pipeline uses this
  to enqueue files for transport.  ``None`` = no transport.
- Files written before a crash are already enqueued via the callback.
  No waiting for ``close()``.

Write timing: most formats write eagerly on ``RunEnded`` so the file
is ready before ``close()``.  ``close()`` is the safety net — it
writes if ``RunEnded`` was never received (crash, partial replay).
CSV writes only on ``close()`` since it has no RunEnded dependency.

Null values (``value=None``) are format-dependent: CSV → empty string,
JSON → omitted, HDF5/MDF4/TDMS → ``NaN`` with metadata flag,
STDF → ``0.0`` with TEST_FLG invalid bit, ATML → no Datum element.
"""

from __future__ import annotations

from litmus.data.subscribers import (
    EventSubscriber,
    get_subscriber_class,
    list_subscribers,
    register_subscriber,
    replay_to_subscriber,
)

# Report formats handled by litmus.reports (not subscribers).
_REPORT_FORMATS = {"html", "pdf"}


def is_report_format(format_name: str) -> bool:
    """Check if a format is handled by litmus.reports (not a subscriber)."""
    return format_name in _REPORT_FORMATS


__all__ = [
    "EventSubscriber",
    "get_subscriber_class",
    "is_report_format",
    "list_subscribers",
    "register_subscriber",
    "replay_to_subscriber",
]

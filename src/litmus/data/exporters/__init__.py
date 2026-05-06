"""Output format subscribers and utilities.

Import directly from the submodule:

    from litmus.data.exporters.csv_exporter import CsvSubscriber
    from litmus.data.subscribers._base import get_subscriber_class, list_subscribers
    from litmus.data.subscribers.replay import replay_to_subscriber
"""

_REPORT_FORMATS = {"html", "pdf"}


def is_report_format(format_name: str) -> bool:
    """Check if a format is handled by litmus.reports (not a subscriber)."""
    return format_name in _REPORT_FORMATS

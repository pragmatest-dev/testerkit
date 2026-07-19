"""Custom-UI-builder surface for TesterKit.

If you're building a custom NiceGUI page that sits alongside the
built-in operator pages — a project-specific dashboard, a custom
fixture-bringup panel, a measurement-comparison view — reach for
the helpers re-exported here instead of the deep
``testerkit.ui.shared.*`` paths.

Two groups of helpers:

**Layout / table primitives** — match the look-and-feel of the
built-in pages so your custom panel doesn't visually drift::

    from testerkit.ui import page_layout, page_header, data_table, format_datetime

    @ui.page("/my-bringup")
    def my_panel():
        with page_layout():
            page_header("Bringup")
            data_table(columns=[...], rows=[...], row_key="id")

**Live event bindings** — subscribe to the event store / channel
store so your page updates push-style as new samples arrive::

    from testerkit.ui import subscribe, channel_data, bind_channel_store

    @ui.page("/live-temp")
    def temp_panel():
        data = channel_data("oven.temp_c")
        ui.label().bind_text_from(data, "latest", lambda v: f"{v:.1f} °C")

The deep ``testerkit.ui.shared.components`` and
``testerkit.ui.shared.event_binding`` paths still work — they're the
contributor form. User code should use this module.

Operator pages themselves (``/results``, ``/metrics``, etc.) are
registered via ``testerkit.ui.pages.*`` on package import and don't
need to be touched by custom-UI builders.
"""

from __future__ import annotations

from testerkit.ui.shared.components import (
    data_table,
    format_datetime,
    info_field,
    page_header,
    page_layout,
    push_url_state,
)
from testerkit.ui.shared.event_binding import (
    bind_channel_store,
)
from testerkit.ui.shared.event_binding import (
    ui_channel_data as channel_data,
)
from testerkit.ui.shared.event_binding import (
    ui_subscribe as subscribe,
)

__all__ = [
    "bind_channel_store",
    "channel_data",
    "data_table",
    "format_datetime",
    "info_field",
    "page_header",
    "page_layout",
    "push_url_state",
    "subscribe",
]

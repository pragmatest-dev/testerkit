"""``testerkit.ui`` exposes the custom-UI-builder helpers at one shallow path."""

from __future__ import annotations

import testerkit.ui as ui_surface
from testerkit.ui.shared.components import (
    data_table as _data_table,
)
from testerkit.ui.shared.components import (
    format_datetime as _format_datetime,
)
from testerkit.ui.shared.components import (
    info_field as _info_field,
)
from testerkit.ui.shared.components import (
    page_header as _page_header,
)
from testerkit.ui.shared.components import (
    page_layout as _page_layout,
)
from testerkit.ui.shared.components import (
    push_url_state as _push_url_state,
)
from testerkit.ui.shared.event_binding import (
    bind_channel_store as _bind_channel_store,
)
from testerkit.ui.shared.event_binding import (
    ui_channel_data as _ui_channel_data,
)
from testerkit.ui.shared.event_binding import (
    ui_subscribe as _ui_subscribe,
)


def test_layout_helpers_identity() -> None:
    assert ui_surface.page_layout is _page_layout
    assert ui_surface.page_header is _page_header
    assert ui_surface.data_table is _data_table
    assert ui_surface.format_datetime is _format_datetime
    assert ui_surface.info_field is _info_field
    assert ui_surface.push_url_state is _push_url_state


def test_event_helpers_identity_with_aliases() -> None:
    """``subscribe`` / ``channel_data`` are aliases for the ``ui_`` -prefixed forms."""
    assert ui_surface.subscribe is _ui_subscribe
    assert ui_surface.channel_data is _ui_channel_data
    assert ui_surface.bind_channel_store is _bind_channel_store


def test_dunder_all_matches_actual_exports() -> None:
    assert set(ui_surface.__all__) == {
        "bind_channel_store",
        "channel_data",
        "data_table",
        "format_datetime",
        "info_field",
        "page_header",
        "page_layout",
        "push_url_state",
        "subscribe",
    }

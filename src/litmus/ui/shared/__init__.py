"""Shared UI utilities."""

from litmus.ui.shared.components import (
    InstrumentToggle,
    litmus_table,
    table_cell_slot,
    table_col,
)
from litmus.ui.shared.dialogs import create_dialog_container
from litmus.ui.shared.layout import create_layout, create_sidebar
from litmus.ui.shared.services import (
    discover_instrument_types,
    discover_products,
    discover_sequences,
    discover_stations,
    discover_tests,
    get_compatible_stations_for_product,
    get_required_capabilities,
    get_station_capabilities,
    load_product_model,
    load_station_config,
    save_product,
    save_station,
    station_compatible_with_product,
)

__all__ = [
    "InstrumentToggle",
    "create_dialog_container",
    "create_layout",
    "create_sidebar",
    "discover_instrument_types",
    "discover_products",
    "discover_sequences",
    "discover_stations",
    "discover_tests",
    "get_compatible_stations_for_product",
    "get_required_capabilities",
    "get_station_capabilities",
    "load_product_model",
    "litmus_table",
    "load_station_config",
    "save_product",
    "save_station",
    "station_compatible_with_product",
    "table_cell_slot",
    "table_col",
]

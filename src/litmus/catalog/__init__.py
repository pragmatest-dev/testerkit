"""Instrument catalog: structured capability data for real instruments.

This package exports the ``InstrumentCatalogEntry`` model — the typed
representation of a catalog YAML entry. Load/query functions live in
``litmus.store`` (the single YAML I/O layer for the whole project).
"""

from litmus.models.catalog import InstrumentCatalogEntry

__all__ = [
    "InstrumentCatalogEntry",
]

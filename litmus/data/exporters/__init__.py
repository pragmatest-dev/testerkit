"""Pluggable result exporters for converting TestRun to industry formats.

Architecture:
    TestRun → Exporter → file.{stdf,atml,mdf4,hdf5,tdms,csv}

Exporters convert in-memory TestRun models to target file formats.
They work both at session teardown (from live TestRun) and post-hoc
(from Parquet via reconstruct_test_run).

Built-in exporters:
    csv     — stdlib, no extra deps

Optional exporters (install via pip install litmus[stdf], etc.):
    stdf    — Semi-ATE-STDF
    hdf5    — h5py
    tdms    — npTDMS
    mdf4    — asammdf
    atml    — lxml (stdlib XML if lxml unavailable)
"""

from __future__ import annotations

from litmus.data.exporters._base import Exporter, StreamingDestination
from litmus.data.exporters._registry import (
    get_exporter,
    list_exporters,
    register_exporter,
)

__all__ = [
    "Exporter",
    "StreamingDestination",
    "get_exporter",
    "list_exporters",
    "register_exporter",
]

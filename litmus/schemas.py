"""Compatibility alias for :mod:`litmus.schema_export`.

The canonical module is :mod:`litmus.schema_export`. This file exists only
because some callers (and documentation) import from ``litmus.schemas``.
Prefer ``from litmus.schema_export import ...`` in new code.
"""

from litmus.schema_export import SCHEMA_MAP, FileType, export_schemas

__all__ = ["SCHEMA_MAP", "FileType", "export_schemas"]

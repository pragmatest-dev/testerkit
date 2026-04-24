"""Conftest for the PMIC-A23 profiles example.

No setup required — instrument fixtures come from the station YAML,
product-backed limits come from the product YAML, and the local
``drivers/`` package is importable because pytest adds this project
root to ``sys.path``.
"""

from __future__ import annotations

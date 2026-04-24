"""Conftest for the station example.

No setup required — instrument fixtures come from the station YAML
(``stations/demo_station_001.yaml``), limits come from the product
YAML (``products/power_board.yaml``), and the local ``drivers/``
package is importable because pytest adds this project root to
``sys.path``.
"""

from __future__ import annotations

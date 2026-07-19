"""YAML-schema models — types users author and load through :mod:`testerkit.store`.

Each submodule defines one schema family:

* :mod:`testerkit.models.capability`        — instrument capability + spec models
* :mod:`testerkit.models.catalog`           — instrument catalog entries
* :mod:`testerkit.models.enums`             — canonical enum vocabulary
* :mod:`testerkit.models.instrument`        — calibration / discovered-instrument records
* :mod:`testerkit.models.instrument_asset`  — instrument asset YAML files
* :mod:`testerkit.models.part`           — part + characteristic + pin schemas
* :mod:`testerkit.models.part_manifest`  — per-part workflow manifest
* :mod:`testerkit.models.project`           — ``testerkit.yaml`` + profile schema
* :mod:`testerkit.models.station`           — station deployment + station-type template
* :mod:`testerkit.models.test_config`       — sidecar test config + fixture schema

Import directly from the submodule that owns the type. There is no
package-level convenience re-export — the submodule paths are the API.
"""

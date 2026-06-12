"""YAML-schema models — types users author and load through :mod:`litmus.store`.

Each submodule defines one schema family:

* :mod:`litmus.models.capability`        — instrument capability + spec models
* :mod:`litmus.models.catalog`           — instrument catalog entries
* :mod:`litmus.models.enums`             — canonical enum vocabulary
* :mod:`litmus.models.instrument`        — calibration / discovered-instrument records
* :mod:`litmus.models.instrument_asset`  — instrument asset YAML files
* :mod:`litmus.models.part`           — part + characteristic + pin schemas
* :mod:`litmus.models.part_manifest`  — per-part workflow manifest
* :mod:`litmus.models.project`           — ``litmus.yaml`` + profile schema
* :mod:`litmus.models.station`           — station deployment + station-type template
* :mod:`litmus.models.test_config`       — sidecar test config + fixture schema

Import directly from the submodule that owns the type. There is no
package-level convenience re-export — the submodule paths are the API.
"""

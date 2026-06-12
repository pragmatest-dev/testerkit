# Exporter conformance audit (#17)

Audit of the result exporters against their target standards, done before the
0.2.0 tag. Library-backed exporters (STDF/MDF4/TDMS/HDF5) get a conformant
*container* for free; the risk is semantic. CSV/JSON have no external standard.

## Fixed (commit 3795242)

- **JSON** — a `NaN`/`Inf` measurement emitted the bare `NaN`/`Infinity`
  token (invalid JSON, rejected by strict parsers); a `datetime`/`UUID`/`bytes`
  in the free-form `custom_metadata`/`inputs`/`outputs` dicts raised
  `TypeError` mid-export. Fixed with a recursive `_jsonable()` coercion.
- **HDF5** — the same `dict[str, Any]` values were written straight to h5py
  attrs (which reject non-scalars) → crash. Fixed with `_h5_attr()`.
- Regression test in `tests/test_exporters.py::TestExporterRobustness`.

## STDF — minor, deferred

- `MRR.FINISH_T` is hardcoded to epoch 0 (`stdf.py:241`); should be the run
  end time.
- `PARM_FLG` is all-zeros (`stdf.py:108`): pass/fail still rides `TEST_FLG`
  bit 7, but the parametric over-high/under-low bits (PARM_FLG bits 3/4 —
  *not* TEST_FLG, a common mislabel) are never set from a value-vs-limit
  comparison. A reader can still recompute from `RESULT`/`LO_LIMIT`/`HI_LIMIT`.
- Recommended gate: round-trip generated files through PySTDF and assert the
  record stream.

## CSV / TDMS — minor, deferred

- CSV: `NaN`/`Inf` become literal `"nan"`/`"inf"` strings (Excel mis-handles).
- TDMS: outcome strings written with implicit numpy dtype — works, unpinned.

## Clean

CSV escaping (uses `csv.DictWriter`), MDF4/TDMS/HDF5 *structure*, JSON schema
shape, and `_helpers.py` are conformant — verified.

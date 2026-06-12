# Exporter conformance audit (#17)

Audit of the seven result exporters against their target standards, done
before the 0.2.0 tag. Library-backed exporters (STDF/MDF4/TDMS/HDF5) get a
conformant *container* for free; the risk is semantic. ATML is hand-rolled
XML — highest risk. CSV/JSON have no external standard.

## Fixed (commit 3795242)

- **JSON** — a `NaN`/`Inf` measurement emitted the bare `NaN`/`Infinity`
  token (invalid JSON, rejected by strict parsers); a `datetime`/`UUID`/`bytes`
  in the free-form `custom_metadata`/`inputs`/`outputs` dicts raised
  `TypeError` mid-export. Fixed with a recursive `_jsonable()` coercion.
- **HDF5** — the same `dict[str, Any]` values were written straight to h5py
  attrs (which reject non-scalars) → crash. Fixed with `_h5_attr()`.
- Regression test in `tests/test_exporters.py::TestExporterRobustness`.

## ATML — confirmed non-conformant, blocked on the licensed schema

Verified against the authoritative ATML TestResults XSD structure
(`xml.coverpages.org/IEEE-ATML-TestResultsV202.xsd`, the 2007 edition) and a
TestStand-generated 2011 sample. `src/litmus/data/exporters/atml.py`:

1. **Namespace conflation.** The code maps its `tr` prefix to
   `urn:IEEE-1636.1:2011:01:TestResultsCollection` and uses it for every
   element. The standard splits this into **`trc`** =
   `urn:IEEE-1636.1:2011:01:TestResultsCollection` (the root collection),
   **`tr`** = `urn:IEEE-1636.1:2011:01:TestResults` (result content), and
   **`c`** = `urn:IEEE-1671:2010:Common`. So most elements are emitted in the
   wrong namespace.
2. **Outcome as an attribute.** The code sets `status="Passed"` on
   `ResultSet`/`TestGroup`/`Test`. The schema models outcome as a **child
   `<Outcome value="…">` element** (the IEEE 1671 Common `Outcome` type, a
   foundational pattern stable across ATML versions).
3. **Flat UUT attributes.** The code sets `serialNumber`/`partNumber`/… as
   attributes on `<UUT>`. The schema declares `UUT` as an extension of
   `c:ItemInstance` with a required `UutType` attribute and the serial number
   (and friends) as **child elements** inherited from `ItemInstance`.
4. **Non-schema attributes.** `callerName`, `uutPin`, `instrumentName` on
   `Test` are Litmus inventions, not ATML attributes — they belong in a
   proper extension point or must be dropped from the conformant path. (This
   is where the `dut→uut` rename's `uutPin` lives; the rename kept it
   internally consistent but its conformance was always this open question.)

**Why this isn't fixed here.** A *correct* rewrite needs the exact 2011
`ItemInstance` child structure, the `NumericLimitTestResult` nesting, and the
per-element namespace assignment — none of which are derivable without the
schema. The IEEE 1636.1-2013 SIMICA schemas are a **paid** standard and are
not publicly assemblable (the 2007 coverpages XSD has an unresolved
`Common.xsd` import; the IEEE SCC20 directory 404s; search results for the
2011 set are all purchase pages). Guessing the structure would swap a known
non-conformance for an unverified one.

**To finish (gated on schema access):**
1. Obtain the IEEE 1636.1-2013 + IEEE 1671-2010 Common XSD set and vendor it
   under `tests/_schemas/atml/` (with all imports resolvable).
2. Add a test that schema-validates generated XML via `xmlschema` (pure
   Python, handles imports) — the real conformance gate.
3. Rewrite `atml.py` to pass: namespace split, `<Outcome>` elements,
   `ItemInstance`-based UUT, and a proper home for the Litmus extensions.

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

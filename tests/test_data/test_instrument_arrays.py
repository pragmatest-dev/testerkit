"""Tests for instrument identity arrays in Parquet schema."""

from datetime import date

from litmus.data.models import DUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.instruments.models import CalibrationInfo, InstrumentInfo, InstrumentRecord


def _make_record(
    role: str,
    instrument_id: str = "inst_001",
    driver: str | None = "drivers.FakeDriver",
    resource: str = "GPIB::1::INSTR",
    protocol: str = "visa",
    manufacturer: str | None = "Acme",
    model: str | None = "Model100",
    serial: str | None = "SN001",
    firmware: str | None = "v1.0",
    cal_due: date | None = None,
    cal_last: date | None = None,
    certificate: str | None = None,
    lab: str | None = None,
) -> InstrumentRecord:
    return InstrumentRecord(
        role=role,
        instrument_id=instrument_id,
        driver=driver,
        resource=resource,
        protocol=protocol,
        info=InstrumentInfo(
            manufacturer=manufacturer,
            model=model,
            serial=serial,
            firmware=firmware,
        ),
        calibration=CalibrationInfo(
            due_date=cal_due,
            last_cal=cal_last,
            certificate=certificate,
            lab=lab,
        ),
    )


EXPECTED_KEYS = [
    "instr_name",
    "instr_id",
    "instr_driver",
    "instr_resource",
    "instr_protocol",
    "instr_manufacturer",
    "instr_model",
    "instr_serial",
    "instr_firmware",
    "instr_cal_due",
    "instr_cal_last",
    "instr_cal_certificate",
    "instr_cal_lab",
]


class TestBuildInstrumentArrays:
    """Tests for TestRunLogger.build_instrument_arrays()."""

    def test_build_instrument_arrays_13_keys(self):
        """Verify build_instrument_arrays returns all 13 expected keys."""
        from litmus.execution.logger import TestRunLogger

        dmm = _make_record("dmm", instrument_id="keithley_001", serial="SN-DMM")
        psu = _make_record("psu", instrument_id="keysight_001", serial="SN-PSU")

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="test_suite",
            instruments={"dmm": dmm, "psu": psu},
        )

        arrays = logger.build_instrument_arrays()

        assert set(arrays.keys()) == set(EXPECTED_KEYS)
        # All arrays same length
        lengths = [len(v) for v in arrays.values()]
        assert all(length == 2 for length in lengths)

        assert arrays["instr_name"] == ["dmm", "psu"]
        assert arrays["instr_id"] == ["keithley_001", "keysight_001"]
        assert arrays["instr_driver"] == ["drivers.FakeDriver", "drivers.FakeDriver"]
        assert arrays["instr_serial"] == ["SN-DMM", "SN-PSU"]

    def test_build_instrument_arrays_filtered(self):
        """Verify roles filter returns only requested instruments."""
        from litmus.execution.logger import TestRunLogger

        records = {
            "dmm": _make_record("dmm", serial="SN-DMM"),
            "psu": _make_record("psu", serial="SN-PSU"),
            "eload": _make_record("eload", serial="SN-ELOAD"),
        }

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="test_suite",
            instruments=records,
        )

        arrays = logger.build_instrument_arrays(roles=["dmm", "psu"])

        assert len(arrays["instr_name"]) == 2
        assert arrays["instr_name"] == ["dmm", "psu"]
        assert arrays["instr_serial"] == ["SN-DMM", "SN-PSU"]

    def test_build_instrument_arrays_empty(self):
        """No instruments produces empty lists for all 13 keys."""
        from litmus.execution.logger import TestRunLogger

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="test_suite",
        )

        arrays = logger.build_instrument_arrays()

        assert set(arrays.keys()) == set(EXPECTED_KEYS)
        for key in EXPECTED_KEYS:
            assert arrays[key] == [], f"{key} should be empty list"

    def test_build_instrument_arrays_with_calibration(self):
        """Verify calibration fields are correctly populated."""
        from litmus.execution.logger import TestRunLogger

        record = _make_record(
            "dmm",
            cal_due=date(2026, 12, 31),
            cal_last=date(2025, 12, 31),
            certificate="CERT-001",
            lab="NIST",
        )

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="test_suite",
            instruments={"dmm": record},
        )

        arrays = logger.build_instrument_arrays()

        assert arrays["instr_cal_due"] == ["2026-12-31"]
        assert arrays["instr_cal_last"] == ["2025-12-31"]
        assert arrays["instr_cal_certificate"] == ["CERT-001"]
        assert arrays["instr_cal_lab"] == ["NIST"]


class TestSetStepInstruments:
    """Tests for set_step_instruments()."""

    def test_set_step_instruments_caches(self):
        """set_step_instruments caches the filtered arrays."""
        from litmus.execution.logger import TestRunLogger

        records = {
            "dmm": _make_record("dmm"),
            "psu": _make_record("psu"),
            "eload": _make_record("eload"),
        }

        logger = TestRunLogger(
            dut_serial="DUT001",
            station_id="station_001",
            test_sequence_id="test_suite",
            instruments=records,
        )

        result = logger.set_step_instruments(["dmm"])

        assert result["instr_name"] == ["dmm"]
        assert logger._step_instrument_arrays == result


class TestEmptyRowSchemaMatches:
    """Verify _build_empty_row fallback keys match build_instrument_arrays keys."""

    def test_empty_row_schema_matches(self):
        """_build_empty_row fallback should have same instr_* keys as build_instrument_arrays."""
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(results_dir="/tmp/litmus_test_empty_row")

        test_run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
            test_sequence_id="test_suite",
        )

        # Get empty row without instrument_arrays (triggers fallback)
        row = backend._build_empty_row(test_run, instrument_arrays=None)

        # Extract instr_* keys
        row_instr_keys = sorted(k for k in row if k.startswith("instr_"))

        assert row_instr_keys == sorted(EXPECTED_KEYS)


class TestParquetRoundTrip:
    """Test saving and reading back instrument arrays in Parquet."""

    def test_parquet_round_trip(self, tmp_path):
        """Save TestRun with instrument_arrays, read back, verify columns."""
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(results_dir=tmp_path)

        # Build a test run with a step that has instrument arrays
        test_run = TestRun(
            dut=DUT(serial="SN001"),
            station_id="station_001",
            test_sequence_id="test_suite",
        )

        step = TestStep(name="test_voltage")
        vector = TestVector(index=0)
        measurement = Measurement(
            name="vout", value=3.3, units="V", outcome=Outcome.PASS
        )
        vector.measurements.append(measurement)
        step.vectors.append(vector)

        # Set per-step instrument arrays
        step.instrument_arrays = {
            "instr_name": ["dmm"],
            "instr_id": ["keithley_001"],
            "instr_driver": ["drivers.Keithley2000"],
            "instr_resource": ["GPIB::16::INSTR"],
            "instr_protocol": ["visa"],
            "instr_manufacturer": ["Keithley"],
            "instr_model": ["2000"],
            "instr_serial": ["SN-DMM-001"],
            "instr_firmware": ["v1.2.3"],
            "instr_cal_due": ["2026-12-31"],
            "instr_cal_last": ["2025-12-31"],
            "instr_cal_certificate": ["CERT-001"],
            "instr_cal_lab": ["NIST"],
        }
        test_run.steps.append(step)

        # Save
        parquet_path = backend.save_test_run(test_run)

        # Read back
        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        rows = table.to_pylist()

        assert len(rows) == 1
        row = rows[0]

        # Verify instrument columns
        assert row["instr_name"] == ["dmm"]
        assert row["instr_id"] == ["keithley_001"]
        assert row["instr_driver"] == ["drivers.Keithley2000"]
        assert row["instr_resource"] == ["GPIB::16::INSTR"]
        assert row["instr_manufacturer"] == ["Keithley"]
        assert row["instr_serial"] == ["SN-DMM-001"]
        assert row["instr_cal_due"] == ["2026-12-31"]
        assert row["instr_cal_lab"] == ["NIST"]

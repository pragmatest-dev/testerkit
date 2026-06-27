"""Tests for instruments list<struct> in Parquet schema."""

from datetime import date

from litmus.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector
from litmus.models.instrument import CalibrationInfo, InstrumentInfo, InstrumentRecord


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


EXPECTED_STRUCT_KEYS = {
    "name",
    "id",
    "driver",
    "resource",
    "protocol",
    "manufacturer",
    "model",
    "serial_number",
    "firmware",
    "cal_due",
    "cal_last",
    "cal_certificate",
    "cal_lab",
    "mocked",
}


class TestBuildInstrumentRecords:
    """Tests for RunScope.build_instrument_records()."""

    def test_build_instrument_records_14_keys(self):
        """Verify build_instrument_records returns dicts with all 14 struct keys."""
        from litmus.execution.run_scope import RunScope

        dmm = _make_record("dmm", instrument_id="keithley_001", serial="SN-DMM")
        psu = _make_record("psu", instrument_id="keysight_001", serial="SN-PSU")

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
            instruments={"dmm": dmm, "psu": psu},
        )

        records = logger.build_instrument_records()

        assert len(records) == 2
        for rec in records:
            assert set(rec.keys()) == EXPECTED_STRUCT_KEYS

        names = [r["name"] for r in records]
        ids = [r["id"] for r in records]
        serials = [r["serial_number"] for r in records]

        assert names == ["dmm", "psu"]
        assert ids == ["keithley_001", "keysight_001"]
        assert serials == ["SN-DMM", "SN-PSU"]

    def test_build_instrument_records_filtered(self):
        """Verify roles filter returns only requested instruments."""
        from litmus.execution.run_scope import RunScope

        records_input = {
            "dmm": _make_record("dmm", serial="SN-DMM"),
            "psu": _make_record("psu", serial="SN-PSU"),
            "eload": _make_record("eload", serial="SN-ELOAD"),
        }

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
            instruments=records_input,
        )

        records = logger.build_instrument_records(roles=["dmm", "psu"])

        assert len(records) == 2
        names = [r["name"] for r in records]
        serials = [r["serial_number"] for r in records]
        assert names == ["dmm", "psu"]
        assert serials == ["SN-DMM", "SN-PSU"]

    def test_build_instrument_records_empty(self):
        """No instruments produces empty list."""
        from litmus.execution.run_scope import RunScope

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
        )

        records = logger.build_instrument_records()
        assert records == []

    def test_build_instrument_records_with_calibration(self):
        """Verify calibration fields are correctly populated."""
        from litmus.execution.run_scope import RunScope

        record = _make_record(
            "dmm",
            cal_due=date(2026, 12, 31),
            cal_last=date(2025, 12, 31),
            certificate="CERT-001",
            lab="NIST",
        )

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
            instruments={"dmm": record},
        )

        records = logger.build_instrument_records()

        assert len(records) == 1
        rec = records[0]
        assert rec["cal_due"] == "2026-12-31"
        assert rec["cal_last"] == "2025-12-31"
        assert rec["cal_certificate"] == "CERT-001"
        assert rec["cal_lab"] == "NIST"


class TestSetStepInstruments:
    """Tests for set_step_instruments()."""

    def test_set_step_instruments_caches(self):
        """set_step_instruments caches the filtered records."""
        from litmus.execution.run_scope import RunScope

        instruments_input = {
            "dmm": _make_record("dmm"),
            "psu": _make_record("psu"),
            "eload": _make_record("eload"),
        }

        logger = RunScope(
            uut_serial="UUT001",
            station_id="station_001",
            instruments=instruments_input,
        )

        result = logger.set_step_instruments(["dmm"])

        assert len(result) == 1
        assert result[0]["name"] == "dmm"
        assert logger._step_instrument_records == result


class TestInstrumentsColumn:
    """Verify the instruments nested list<struct> column in run rows."""

    def test_run_row_has_instruments_list(self):
        """_build_run_row should have an instruments key with a list value."""
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(data_dir="/tmp/litmus_test_run_row")

        test_run = TestRun(
            uut=UUT(serial="SN001"),
            station_id="station_001",
        )

        row = backend._build_run_row(test_run, instrument_records=None)

        assert "instruments" in row
        assert isinstance(row["instruments"], list)
        assert row["instruments"] == []


class TestParquetRoundTrip:
    """Test saving and reading back instrument records in Parquet."""

    def test_parquet_round_trip(self, tmp_path):
        """Save TestRun with instrument_records, read back, verify nested struct."""
        from litmus.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(data_dir=tmp_path)

        test_run = TestRun(
            uut=UUT(serial="SN001"),
            station_id="station_001",
        )

        step = TestStep(name="test_voltage")
        vector = TestVector(index=0)
        measurement = Measurement(name="vout", value=3.3, unit="V", outcome=Outcome.PASSED)
        vector.measurements.append(measurement)
        step.vectors.append(vector)

        step.instrument_records = [
            {
                "name": "dmm",
                "id": "keithley_001",
                "driver": "drivers.Keithley2000",
                "resource": "GPIB::16::INSTR",
                "protocol": "visa",
                "manufacturer": "Keithley",
                "model": "2000",
                "serial_number": "SN-DMM-001",
                "firmware": "v1.2.3",
                "cal_due": "2026-12-31",
                "cal_last": "2025-12-31",
                "cal_certificate": "CERT-001",
                "cal_lab": "NIST",
                "mocked": False,
            }
        ]
        test_run.steps.append(step)

        parquet_path = backend.save_test_run(test_run)

        import pyarrow.parquet as pq

        table = pq.read_table(parquet_path)
        rows = table.to_pylist()

        # v2 nested schema: 1 run + 1 step + 1 scope vector
        assert len(rows) == 3
        vec_rows = [r for r in rows if r["record_type"] == "vector"]
        assert len(vec_rows) == 1
        row = vec_rows[0]
        assert [m["name"] for m in row["measurements"]] == ["vout"]

        instruments = row["instruments"]
        assert len(instruments) == 1
        inst = instruments[0]
        assert inst["name"] == "dmm"
        assert inst["id"] == "keithley_001"
        assert inst["driver"] == "drivers.Keithley2000"
        assert inst["resource"] == "GPIB::16::INSTR"
        assert inst["manufacturer"] == "Keithley"
        assert inst["serial_number"] == "SN-DMM-001"
        assert inst["cal_due"] == "2026-12-31"
        assert inst["cal_lab"] == "NIST"
        assert inst["mocked"] is False

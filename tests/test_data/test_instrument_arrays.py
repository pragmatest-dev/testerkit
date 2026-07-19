"""Tests for instruments list<struct> in Parquet schema."""

from testerkit.data.models import UUT, Measurement, Outcome, TestRun, TestStep, TestVector


class TestInstrumentsColumn:
    """Verify the instruments nested list<struct> column in run rows."""

    def test_run_row_has_instruments_list(self):
        """_build_run_row should have an instruments key with a list value."""
        from testerkit.data.backends.parquet import ParquetBackend

        backend = ParquetBackend(data_dir="/tmp/testerkit_run_row")

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
        from testerkit.data.backends.parquet import ParquetBackend

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

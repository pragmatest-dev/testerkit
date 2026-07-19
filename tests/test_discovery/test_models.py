"""Tests for discovery models."""

from testerkit.models.instrument import InstrumentInfo


class TestInstrumentInfo:
    """Tests for InstrumentInfo model used by discovery."""

    def test_minimal(self):
        info = InstrumentInfo()
        assert info.manufacturer is None
        assert info.model is None
        assert info.serial is None
        assert info.firmware is None

    def test_fully_populated(self):
        info = InstrumentInfo(
            manufacturer="Keysight",
            model="34465A",
            serial="MY12345678",
            firmware="A.02.14-02.40-02.14-00.49-04-01",
        )
        assert info.manufacturer == "Keysight"
        assert info.model == "34465A"
        assert info.serial == "MY12345678"
        assert info.firmware == "A.02.14-02.40-02.14-00.49-04-01"

    def test_bool_false_when_empty(self):
        assert not InstrumentInfo()

    def test_bool_true_when_populated(self):
        assert InstrumentInfo(manufacturer="Keysight")

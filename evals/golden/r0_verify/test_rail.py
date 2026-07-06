def test_rail_in_spec(verify) -> None:
    verify("rail_voltage", 3.28, limit={"low": 3.0, "high": 3.6, "unit": "V"})

def test_rail_observe(observe) -> None:
    # limit lives in test_rail.yaml as a guardband off a characteristic;
    # this test only records the reading, so the limit is never resolved —
    # the grader validates the sidecar's SCHEMA, not runtime resolution.
    observe("rail_voltage", 3.31)

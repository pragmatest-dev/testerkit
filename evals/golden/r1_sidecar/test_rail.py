def test_rail_in_spec(verify) -> None:
    # limit lives in test_rail.yaml; verify resolves it from the sidecar
    verify("rail_voltage", 3.28)

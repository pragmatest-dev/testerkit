"""Simulated VISA instruments using pyvisa-sim."""

import tempfile
from pathlib import Path

# pyvisa-sim YAML definition for a simulated DMM
DMM_SIM_YAML = """\
spec: "1.0"
devices:
  dmm:
    eom:
      TCPIP INSTR:
        q: "\\n"
        r: "\\n"
    dialogues:
      - q: "*IDN?"
        r: "Litmus,SimDMM,SN001,1.0"
      - q: "MEAS:VOLT:DC?"
        r: "5.0012"
      - q: "MEAS:CURR:DC?"
        r: "0.1003"
      - q: "MEAS:RES?"
        r: "1000.5"
      - q: "MEAS:FRES?"
        r: "999.8"

resources:
  TCPIP::192.168.1.100::INSTR:
    device: dmm
"""


def get_sim_resource_manager() -> str:
    """Return visa_library string for DMM simulation.

    Creates a temporary pyvisa-sim configuration file and returns
    the path in the format required by pyvisa ResourceManager.

    Returns:
        String to pass as visa_library parameter (e.g., "/path/to/dmm.yaml@sim")
    """
    sim_dir = Path(tempfile.gettempdir()) / "litmus_sim"
    sim_dir.mkdir(exist_ok=True)
    sim_file = sim_dir / "dmm.yaml"
    sim_file.write_text(DMM_SIM_YAML)
    return f"{sim_file}@sim"


def get_simulated_resource() -> str:
    """Return the default simulated DMM resource string."""
    return "TCPIP::192.168.1.100::INSTR"

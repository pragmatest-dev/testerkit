"""Protocol for switch/relay matrix drivers.

Channel-list style (not IviSwtch connect/disconnect) because:
- Universal: works for NI relay drivers, SCPI matrices, Pickering
- Fixture YAML specifies exact channels — no path-finding needed
- Users with IviSwtch drivers write a thin adapter
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SwitchDriver(Protocol):
    """Protocol for switch/relay matrix drivers.

    Implementations close/open named channels (crosspoints) on a
    switch matrix. Channel names are opaque strings matching the
    fixture YAML — the driver maps them to hardware commands.

    Example implementation for SCPI matrix::

        class ScpiMatrix:
            def close_channels(self, channels: list[str]) -> None:
                self.write(f"ROUT:CLOS (@{','.join(channels)})")

            def open_channels(self, channels: list[str]) -> None:
                self.write(f"ROUT:OPEN (@{','.join(channels)})")

            def open_all(self) -> None:
                self.write("ROUT:OPEN:ALL")
    """

    def close_channels(self, channels: list[str]) -> None:
        """Close (connect) the specified switch channels."""
        ...

    def open_channels(self, channels: list[str]) -> None:
        """Open (disconnect) the specified switch channels."""
        ...

    def open_all(self) -> None:
        """Open all channels on the switch (safe state)."""
        ...

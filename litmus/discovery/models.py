"""Models for discovered instruments."""

from pydantic import BaseModel


class DiscoveredInstrument(BaseModel):
    """A discovered VISA instrument.

    Represents an instrument found during scanning, with parsed
    identification information from the *IDN? response.
    """

    resource: str
    idn: str | None = None
    manufacturer: str | None = None
    model: str | None = None
    serial: str | None = None
    firmware: str | None = None
    reachable: bool = True
    error: str | None = None

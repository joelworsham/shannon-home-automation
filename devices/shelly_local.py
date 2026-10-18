"""Read-only local RPC integration for Shelly Plug US Gen4.

IMPORTANT: No switch-control request is implemented or allowed in this module.
The plug's relay should remain ON. Power measurements are observational only.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from urllib.parse import urlsplit

import requests
from requests.auth import HTTPDigestAuth


class ShellyReadError(RuntimeError):
    """Status missing, invalid, or unsafe to interpret as a fan observation."""


def validate_local_host(host: str) -> str:
    """Accept a LAN host/IP with optional port, not a full URL or path."""
    host = host.strip()
    if not host or any(c.isspace() for c in host) or '/' in host or '@' in host:
        raise ValueError("Device host must be a LAN hostname/IP (optional :port), not a URL")
    parsed = urlsplit('http://' + host)
    if (parsed.scheme != 'http' or not parsed.hostname or
            parsed.path or parsed.query or parsed.fragment):
        raise ValueError("Device host must be a hostname/IP (optional :port)")
    try:
        port = parsed.port
    except ValueError as exc:
        raise ValueError("Invalid device port") from exc
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid device port")
    return host


def _optional_nonnegative_number(obj: dict, key: str) -> float | None:
    value = obj.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ShellyReadError(f"Invalid Shelly {key} measurement")
    v = float(value)
    if not isfinite(v) or v < 0:
        raise ShellyReadError(f"Invalid Shelly {key} measurement")
    return v


@dataclass(frozen=True)
class ShellyPowerStatus:
    watts: float
    relay_on: bool
    voltage: float | None
    current_amps: float | None
    read_at_utc: str


class ShellyPlugUSGen4:
    """Only GET /rpc/Switch.GetStatus?id=0. Never sets output power."""

    def __init__(self, host: str, *, username: str = 'admin', password: str | None = None,
                 timeout: float = 5, session=None):
        self.host = validate_local_host(host)
        if timeout <= 0 or not isfinite(timeout):
            raise ValueError("timeout must be positive and finite")
        self.timeout = timeout
        self.session = session or requests.Session()
        self.auth = HTTPDigestAuth(username, password) if password else None

    def read_status(self) -> ShellyPowerStatus:
        response = self.session.get(
            f'http://{self.host}/rpc/Switch.GetStatus',
            params={'id': 0}, timeout=self.timeout, auth=self.auth,
        )
        response.raise_for_status()
        data = response.json()
        if not isinstance(data, dict):
            raise ShellyReadError("Invalid Shelly status response")
        relay_on = data.get('output')
        if not isinstance(relay_on, bool):
            raise ShellyReadError("Shelly status missing relay output field")
        watts = _optional_nonnegative_number(data, 'apower')
        if watts is None:
            raise ShellyReadError("Shelly status missing apower field")
        return ShellyPowerStatus(
            watts=watts, relay_on=relay_on,
            voltage=_optional_nonnegative_number(data, 'voltage'),
            current_amps=_optional_nonnegative_number(data, 'current'),
            read_at_utc=datetime.now(timezone.utc).isoformat(),
        )

    def read_watts(self) -> float:
        status = self.read_status()
        if not status.relay_on:
            # A plug switched OFF is a wiring/power issue, not evidence that the
            # fan's own IR power button has switched it to standby.
            raise ShellyReadError(
                "Shelly relay is OFF: no wattage inference; restore outlet power manually"
            )
        return status.watts

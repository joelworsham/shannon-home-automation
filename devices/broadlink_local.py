"""BroadLink RM4 Pro local-network adapter and learned-command codebook.

Local IR/RF replay is disabled unless a caller EXPLICITLY enables transmission.
The main controller does not create this adapter and remains DRY RUN.
"""
from __future__ import annotations

import base64
import binascii
import json
from pathlib import Path
from typing import Callable, Any

from devices.shelly_local import validate_local_host

# Logical names, not BroadLink packet data. Capture actual packets after delivery.
VORNADO_COMMANDS = frozenset({
    'power_toggle', 'direction_toggle', 'speed_up', 'speed_down',
})
CEILING_COMMANDS = frozenset({
    'ceiling_low', 'ceiling_medium', 'ceiling_high', 'ceiling_stop',
    'ceiling_light_toggle',
})
SUPPORTED_COMMANDS = VORNADO_COMMANDS | CEILING_COMMANDS


class CommandBook:
    def __init__(self, packets: dict[str, bytes]):
        for name, data in packets.items():
            if name not in SUPPORTED_COMMANDS:
                raise ValueError(f"Unknown learned command name: {name}")
            if not isinstance(data, bytes) or not 4 <= len(data) <= 16384:
                raise ValueError(f"Invalid learned packet for {name}")
        self._packets = dict(packets)

    @classmethod
    def from_file(cls, path: str | Path) -> 'CommandBook':
        """Read captured base64 packets; blank example entries are ignored."""
        with Path(path).open(encoding='utf-8') as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            raise ValueError("IR/RF codebook must be a JSON object")
        packets: dict[str, bytes] = {}
        for name, code in raw.items():
            if name not in SUPPORTED_COMMANDS:
                raise ValueError(f"Unknown learned command name: {name}")
            if not isinstance(code, str):
                raise ValueError(f"Invalid base64 data for {name}")
            if not code.strip():
                continue
            try:
                packet = base64.b64decode(code, validate=True)
            except binascii.Error as exc:
                raise ValueError(f"Invalid base64 data for {name}") from exc
            packets[name] = packet
        return cls(packets)

    def names(self) -> tuple[str, ...]:
        return tuple(sorted(self._packets))

    def packet(self, command: str) -> bytes:
        if command not in SUPPORTED_COMMANDS:
            raise ValueError(f"Unsupported remote command: {command}")
        if command not in self._packets:
            raise KeyError(f"No learned IR/RF packet stored for {command}")
        return self._packets[command]


def _connect_device(host: str) -> Any:
    import broadlink
    # hello is targeted unicast discovery, helpful on networks where broadcast
    # discovery is blocked; the RM4 Pro must be unlocked for local control.
    device = broadlink.hello(host)
    if device is None or not hasattr(device, 'auth'):
        raise RuntimeError("BroadLink device not found")
    if not hasattr(device, 'send_data'):
        raise RuntimeError("Discovered BroadLink device is not an IR/RF remote")
    authenticated = device.auth()
    if authenticated is False:
        raise RuntimeError("BroadLink local authentication failed")
    return device


class BroadLinkLocal:
    """Local RM4 Pro adapter; probe() is safe, send() requires opt-in."""

    def __init__(self, host: str, book: CommandBook | None = None,
                 *, permit_transmit: bool = False,
                 connector: Callable[[str], Any] | None = None):
        self.host = validate_local_host(host)
        self.book = book or CommandBook({})
        self.permit_transmit = permit_transmit
        self.connector = connector or _connect_device
        self._device = None

    def probe(self) -> str:
        """Connect/authenticate only. Does NOT send IR/RF commands."""
        if self._device is None:
            self._device = self.connector(self.host)
        if not hasattr(self._device, 'send_data'):
            raise RuntimeError("Connected device cannot transmit remote codes")
        return type(self._device).__name__

    def send(self, command: str) -> None:
        if not self.permit_transmit:
            raise PermissionError("IR/RF sending explicitly disabled")
        # Validate before contacting hardware; never send unknown/incomplete data.
        packet = self.book.packet(command)
        self.probe()
        self._device.send_data(packet)
        # send_data success is NOT evidence the real fan changed state.

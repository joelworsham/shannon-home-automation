"""BroadLink 0.19-compatible UDP request with exactly one send attempt.

The upstream transport retries on timeout. That is unsuitable for toggles:
loss of an acknowledgement does not mean the IR packet was not emitted.
"""
import socket

from broadlink.exceptions import DataValidationError, NetworkTimeoutError


def send_packet_once(device, packet_type: int, payload: bytes) -> bytes:
    device.count = ((device.count + 1) | 0x8000) & 0xFFFF
    packet = bytearray(0x38)
    packet[:8] = bytes.fromhex('5aa5aa555aa5aa55')
    packet[0x24:0x26] = device.devtype.to_bytes(2, 'little')
    packet[0x26:0x28] = packet_type.to_bytes(2, 'little')
    packet[0x28:0x2A] = device.count.to_bytes(2, 'little')
    packet[0x2A:0x30] = device.mac[::-1]
    packet[0x30:0x34] = device.id.to_bytes(4, 'little')
    packet[0x34:0x36] = (sum(payload, 0xBEAF) & 0xFFFF).to_bytes(2, 'little')
    packet.extend(device.encrypt(payload + bytes((-len(payload)) % 16)))
    packet[0x20:0x22] = (sum(packet, 0xBEAF) & 0xFFFF).to_bytes(2, 'little')
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as conn:
        conn.settimeout(device.timeout)
        # Connected UDP accepts replies only from this device's endpoint.
        conn.connect(device.host)
        conn.send(packet)
        try:
            response = conn.recv(2048)
        except socket.timeout as exc:
            raise NetworkTimeoutError(-4000, 'Unconfirmed transmission; no retry') from exc
    if len(response) < 0x38:
        raise DataValidationError(-4007, 'Truncated BroadLink response')
    checksum = sum(response, 0xBEAF) - sum(response[0x20:0x22])
    if int.from_bytes(response[0x20:0x22], 'little') != checksum & 0xFFFF:
        raise DataValidationError(-4008, 'Invalid BroadLink response checksum')
    if response[0x28:0x2A] != packet[0x28:0x2A]:
        raise DataValidationError(-4010, 'Mismatched BroadLink response counter')
    return response

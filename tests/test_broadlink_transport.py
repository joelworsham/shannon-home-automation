import socket
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from broadlink.exceptions import DataValidationError, NetworkTimeoutError
from devices.broadlink_transport import send_packet_once
from devices.broadlink_local import BroadLinkLocal, CommandBook


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.device = SimpleNamespace(count=1, devtype=0x6026, mac=b'123456', id=1,
                                      host=('192.0.2.1', 80), timeout=1, encrypt=lambda b: b)
        self.conn = Mock()
        self.conn.__enter__ = Mock(return_value=self.conn)
        self.conn.__exit__ = Mock(return_value=False)
        p = patch('devices.broadlink_transport.socket.socket', return_value=self.conn)
        p.start()
        self.addCleanup(p.stop)

    def reply(self, counter=0x8002):
        result = bytearray(0x38)
        result[0x28:0x2A] = counter.to_bytes(2, 'little')
        result[0x20:0x22] = (sum(result, 0xBEAF) & 0xFFFF).to_bytes(2, 'little')
        return bytes(result)

    def test_one_datagram_valid_response(self):
        self.conn.recv.return_value = self.reply()
        self.assertEqual(send_packet_once(self.device, 0x6A, b'test'), self.reply())
        self.conn.send.assert_called_once()
        self.conn.connect.assert_called_once_with(self.device.host)
        packet = self.conn.send.call_args.args[0]
        self.assertEqual(packet[0x28:0x2A], b'\x02\x80')
        self.assertEqual(int.from_bytes(packet[0x20:0x22], 'little'),
                         (sum(packet, 0xBEAF) - sum(packet[0x20:0x22])) & 0xFFFF)

    def test_lost_ack_never_retries(self):
        self.conn.recv.side_effect = socket.timeout()
        with self.assertRaises(NetworkTimeoutError):
            send_packet_once(self.device, 0x6A, b'test')
        self.conn.send.assert_called_once()

    def test_corrupt_short_or_wrong_reply_never_retries(self):
        for response in (b'', bytes(56), self.reply(counter=0x8003)):
            with self.subTest(response_length=len(response)):
                self.device.count = 1
                self.conn.reset_mock()
                self.conn.recv.return_value = response
                with self.assertRaises(DataValidationError):
                    send_packet_once(self.device, 0x6A, b'test')
                self.conn.send.assert_called_once()

    def test_adapter_routes_send_and_restores_transport_on_failure(self):
        original = Mock()
        self.device.send_packet = original
        self.device.send_data = lambda data: self.device.send_packet(0x6A, data)
        adapter = BroadLinkLocal('192.0.2.1', CommandBook({'power_toggle': b'1234'}),
                                 permit_transmit=True, connector=lambda _: self.device)
        self.conn.recv.side_effect = socket.timeout()
        with self.assertRaises(NetworkTimeoutError):
            adapter.send('power_toggle')
        self.assertIs(self.device.send_packet, original)
        original.assert_not_called()
        self.conn.send.assert_called_once()

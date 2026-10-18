"""Offline local-device adapter contract and safety tests, no network/hardware."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock

from devices.shelly_local import ShellyPlugUSGen4, ShellyReadError, validate_local_host
from devices.broadlink_local import BroadLinkLocal, CommandBook


class FakeResponse:
    def __init__(self, payload):
        self.payload = payload
        self.raises = 0

    def raise_for_status(self):
        self.raises += 1

    def json(self):
        return self.payload


class ShellyLocalTests(unittest.TestCase):
    def setUp(self):
        self.session = Mock()
        self.response = FakeResponse({'id': 0, 'output': True, 'apower': 12.5,
                                      'voltage': 122.1, 'current': 0.12})
        self.session.get.return_value = self.response
        self.device = ShellyPlugUSGen4('shelly.local', session=self.session)

    def test_uses_only_safe_rpc_get(self):
        s = self.device.read_status()
        self.assertEqual(s.watts, 12.5)
        self.assertTrue(s.relay_on)
        self.assertEqual(s.voltage, 122.1)
        self.assertEqual(self.response.raises, 1)
        self.session.get.assert_called_once_with(
            'http://shelly.local/rpc/Switch.GetStatus', params={'id': 0}, timeout=5, auth=None)
        self.assertFalse(self.session.post.called)
        self.assertFalse(hasattr(self.device, 'switch_off'))
        self.assertFalse(hasattr(self.device, 'set_power'))

    def test_power_read_is_read_only(self):
        self.assertEqual(self.device.read_watts(), 12.5)
        self.session.get.assert_called_once()

    def test_relay_off_blocks_power_inference(self):
        self.session.get.return_value = FakeResponse({'output': False, 'apower': 0})
        self.assertFalse(self.device.read_status().relay_on)
        with self.assertRaisesRegex(ShellyReadError, 'relay is OFF'):
            self.device.read_watts()

    def test_missing_watts_rejected(self):
        self.session.get.return_value = FakeResponse({'output': True})
        with self.assertRaisesRegex(ShellyReadError, 'apower'):
            self.device.read_status()

    def test_nan_watts_rejected(self):
        self.session.get.return_value = FakeResponse({'output': True, 'apower': float('nan')})
        with self.assertRaises(ShellyReadError):
            self.device.read_status()

    def test_negative_watts_rejected(self):
        self.session.get.return_value = FakeResponse({'output': True, 'apower': -1})
        with self.assertRaises(ShellyReadError):
            self.device.read_status()

    def test_numeric_string_watts_rejected(self):
        self.session.get.return_value = FakeResponse({'output': True, 'apower': '5'})
        with self.assertRaises(ShellyReadError):
            self.device.read_status()

    def test_bad_json_rejected(self):
        self.session.get.return_value = FakeResponse([])
        with self.assertRaises(ShellyReadError):
            self.device.read_status()

    def test_only_local_host_format(self):
        for invalid in ('', 'https://example.com', 'localhost/rpc/Switch.Set', 'admin@device',
                        'host?query=1', 'host name', 'host:99999'):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                validate_local_host(invalid)
        self.assertEqual(validate_local_host('192.168.1.2'), '192.168.1.2')

    def test_digest_auth_optional(self):
        device = ShellyPlugUSGen4('192.168.1.2', password='not-real', session=self.session)
        device.read_status()
        self.assertEqual(self.session.get.call_args.kwargs['auth'].password, 'not-real')


class FakeRM4:
    def __init__(self):
        self.sent = []

    def send_data(self, packet):
        self.sent.append(packet)


class BroadLinkLocalTests(unittest.TestCase):
    def setUp(self):
        self.fake = FakeRM4()
        self.connector = Mock(return_value=self.fake)
        self.packet = b'\x26\x00\x04\x00hello'
        self.book = CommandBook({'power_toggle': self.packet})

    def test_probe_cannot_send(self):
        bl = BroadLinkLocal('192.168.1.9', self.book, connector=self.connector)
        self.assertEqual(bl.probe(), 'FakeRM4')
        self.assertEqual(self.fake.sent, [])
        self.connector.assert_called_once_with('192.168.1.9')

    def test_default_send_denied_without_connecting(self):
        bl = BroadLinkLocal('192.168.1.9', self.book, connector=self.connector)
        with self.assertRaises(PermissionError):
            bl.send('power_toggle')
        self.connector.assert_not_called()
        self.assertEqual(self.fake.sent, [])

    def test_transmit_requires_explicit_opt_in_and_learned_code(self):
        bl = BroadLinkLocal('192.168.1.9', self.book, connector=self.connector,
                            permit_transmit=True)
        bl.send('power_toggle')
        self.assertEqual(self.fake.sent, [self.packet])

    def test_missing_code_never_connects(self):
        bl = BroadLinkLocal('192.168.1.9', self.book, connector=self.connector,
                            permit_transmit=True)
        with self.assertRaises(KeyError):
            bl.send('speed_up')
        self.connector.assert_not_called()

    def test_unknown_command_never_connects(self):
        bl = BroadLinkLocal('192.168.1.9', self.book, connector=self.connector,
                            permit_transmit=True)
        with self.assertRaises(ValueError):
            bl.send('dangerous_other_command')
        self.connector.assert_not_called()

    def test_codebook_example_is_empty(self):
        path = Path(__file__).resolve().parent.parent / 'config' / 'remote_codes.example.json'
        self.assertEqual(CommandBook.from_file(path).names(), ())

    def test_codebook_base64_loading(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'codes.json'
            p.write_text(json.dumps({'power_toggle': base64.b64encode(self.packet).decode(),
                                     'speed_up': ''}))
            book = CommandBook.from_file(p)
            self.assertEqual(book.names(), ('power_toggle',))
            self.assertEqual(book.packet('power_toggle'), self.packet)

    def test_codebook_rejects_unknown_or_invalid(self):
        with tempfile.TemporaryDirectory() as t:
            p = Path(t) / 'codes.json'
            p.write_text('{"accidental": ""}')
            with self.assertRaises(ValueError):
                CommandBook.from_file(p)
            p.write_text('{"power_toggle": "not-valid-base64!"}')
            with self.assertRaises(ValueError):
                CommandBook.from_file(p)
            p.write_text('{"power_toggle": "YQ=="}')
            with self.assertRaises(ValueError):
                CommandBook.from_file(p)


if __name__ == '__main__':
    unittest.main()

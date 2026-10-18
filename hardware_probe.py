"""Opt-in read-only local device checks. NEVER transmits IR/RF or switches Shelly power.

Running without flags makes no network requests and does not change the DB.
"""
from __future__ import annotations

import argparse
import os
from dataclasses import asdict
from pathlib import Path

from dotenv import load_dotenv

from database import save_hardware_observation
from devices.broadlink_local import BroadLinkLocal, CommandBook
from devices.shelly_local import ShellyPlugUSGen4


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--shelly', action='store_true', help='Read Shelly status via GET (does not switch)')
    parser.add_argument('--broadlink', action='store_true', help='Discover/authenticate RM4 Pro (does not send)')
    parser.add_argument('--codes', action='store_true', help='List learned IR/RF code names (offline)')
    args = parser.parse_args()
    if not (args.shelly or args.broadlink or args.codes):
        parser.print_help()
        return
    load_dotenv()
    if args.codes:
        path = Path(os.getenv('BROADLINK_CODES_FILE', 'data/remote_codes.json'))
        if not path.is_file():
            print(f'No learned codes yet: {path} (expected before Tuesday)')
        else:
            book = CommandBook.from_file(path)
            print('Learned command names:', ', '.join(book.names()) or '(none)')
    if args.shelly:
        host = os.getenv('SHELLY_HOST', '').strip()
        if not host:
            parser.error('Set SHELLY_HOST in .env before using --shelly')
        try:
            dev = ShellyPlugUSGen4(host, username=os.getenv('SHELLY_USERNAME') or 'admin',
                                   password=os.getenv('SHELLY_PASSWORD') or None)
            status = dev.read_status()
            data = asdict(status)
            save_hardware_observation(device='shelly', result='ok', status=data)
            print(f"Shelly read-only: {status.watts:.2f} W; outlet relay "
                  f"{'ON' if status.relay_on else 'OFF — inspect this!'}")
        except Exception as exc:
            save_hardware_observation(device='shelly', result='error', error=exc)
            raise
    if args.broadlink:
        host = os.getenv('BROADLINK_HOST', '').strip()
        if not host:
            parser.error('Set BROADLINK_HOST in .env before using --broadlink')
        try:
            blaster = BroadLinkLocal(host)  # permit_transmit=False ALWAYS
            name = blaster.probe()
            save_hardware_observation(device='broadlink', result='ok', status={'model_class': name})
            print(f'BroadLink local connection successful: {name} (no commands sent)')
        except Exception as exc:
            save_hardware_observation(device='broadlink', result='error', error=exc)
            raise


if __name__ == '__main__':
    main()

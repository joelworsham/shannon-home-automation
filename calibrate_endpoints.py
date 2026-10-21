"""Record human-labelled endpoint calibration. Read-only: no IR or relay commands."""
import argparse
from dataclasses import asdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import time
import uuid

from dotenv import load_dotenv
from database import save_hardware_observation
from devices.shelly_local import ShellyPlugUSGen4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--state', required=True, choices=('standby', 'intake-low', 'intake-high'))
    parser.add_argument('--confirmed-settled', action='store_true', required=True,
                        help='You physically checked state and allowed at least 30 seconds to settle')
    args = parser.parse_args()
    load_dotenv()
    meter = ShellyPlugUSGen4(os.getenv('SHELLY_HOST', ''),
        username=os.getenv('SHELLY_USERNAME') or 'admin', password=os.getenv('SHELLY_PASSWORD') or None)
    trial = str(uuid.uuid4())
    samples = []
    directory = Path('data/endpoint_calibration')
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f'{args.state}-{trial}.json'
    record = {'trial': trial, 'state': args.state, 'source': 'human_confirmed_settled',
              'started_at_utc': datetime.now(timezone.utc).isoformat(), 'complete': False, 'samples': samples}
    # Save partial trials too; only complete trials may be used for calibration.
    for index in range(12):
        status = meter.read_status()
        if not status.relay_on:
            raise SystemExit('Shelly outlet is OFF; calibration stopped.')
        sample = asdict(status)
        save_hardware_observation(device='shelly', result='ok', status={
            **sample, 'purpose': 'endpoint_calibration', 'trial': trial,
            'human_confirmed_state': args.state})
        samples.append(sample)
        path.write_text(json.dumps(record, indent=2) + '\n')
        print(f'{index + 1}/12: {status.watts:.2f} W', flush=True)
        if index < 11:
            time.sleep(3)
    record['complete'] = True
    record['finished_at_utc'] = datetime.now(timezone.utc).isoformat()
    path.write_text(json.dumps(record, indent=2) + '\n')
    watts = [s['watts'] for s in samples]
    print(f'{args.state}: {min(watts):.2f}–{max(watts):.2f} W; mean {statistics.mean(watts):.2f} W')
    print('Saved locally. No thresholds enabled; no commands sent.')


if __name__ == '__main__':
    main()

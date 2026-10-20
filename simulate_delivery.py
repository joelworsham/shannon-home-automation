"""Exercise wake-aware delivery offline with fake hardware and disposable history."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import tempfile

import database
from climate_policy import FanState
from delivery_store import DeliveryBlocked
from safety_state import PowerObservation
from settings import DeliverySettings
from wake_delivery import WakeAwareDelivery


def main():
    seconds = [0.0]
    running = [True]
    awake_until = [0.0]
    commands = []

    def now():
        return datetime(2026, 10, 20, tzinfo=timezone.utc) + timedelta(seconds=seconds[0])

    def sleep(duration):
        seconds[0] += duration

    class SimulatedFan:
        def send(self, command):
            commands.append(command)
            was_awake = seconds[0] < awake_until[0]
            awake_until[0] = seconds[0] + 6
            if was_awake and command == 'power_toggle':
                running[0] = not running[0]

    def observe():
        return PowerObservation(running[0], now(), 'intake', 2)

    def engine():
        return WakeAwareDelivery(SimulatedFan(), settings=DeliverySettings(enabled=True),
                                 monotonic=lambda: seconds[0], sleep=sleep, utcnow=now)

    original_path = database.DB_PATH
    try:
        with tempfile.TemporaryDirectory() as directory:
            database.DB_PATH = Path(directory) / 'simulation.sqlite3'
            delivery = engine()
            first = delivery.execute(FanState(), observe, exclusive_control=True)
            sleep(0.1)
            delivery.confirm_power(first, observe())
            print('OFF confirmed with fake evidence; commands:', ', '.join(commands))
            delivery.execute(FanState('intake', 2), observe, exclusive_control=True)
            print('ON attempted inside awake window; commands:', ', '.join(commands))
            try:
                engine().execute(FanState(), observe, exclusive_control=True)
            except DeliveryBlocked:
                print('Restart correctly blocked: ON attempt still needs confirmation.')
            print('Offline only: no network, no IR, temporary database removed on exit.')
    finally:
        database.DB_PATH = original_path


if __name__ == '__main__':
    main()

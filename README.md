# Bedroom climate automation

A custom Python controller for a Vornado Transom window fan, using the existing
**original-generation Flair Puck** for bedroom temperature, and **Open-Meteo**
for outdoor conditions. Historical readings and control decisions are stored
in **SQLite**.

> **Current state:** fully **dry run**. It reads live Flair and weather data,
> determines a *desired* fan operating mode/speed, and saves sensor/decision
> history. It **does not transmit IR/RF signals, operate a Shelly plug, or
> control the physical fan**. BroadLink and Shelly integration will come later.

## Operating rules (current)

- Schedule: **10:00 PM–7:30 AM** Indianapolis local time, crossing midnight.
  Timezone: `America/Indiana/Indianapolis` (handles DST).
- Temperature target: **60°F**; restart cooling when bedroom reaches **64°F**.
- Only cool when outside air is at least **5°F cooler** than the bedroom.
- Desired fan speed 1–4 favors quiet, low-speed operation. Increase thresholds:
  about 67°F (speed 2), 70°F (speed 3), 74°F (speed 4); speed changes are
  smoothed with 0.5°F hysteresis and one step per control cycle.
- Desired power and speed are **not proof of actual fan state**. Future Shelly
  wattage monitoring will make fan-running checks possible; airflow direction
  may still require inference and testing.
- Exhaust-assisted warming is an **unimplemented future experiment**.
- Flair's existing Smart Vent behavior is **not altered**.

## Requirements

- Python **3.10+** and Git; macOS for optional `launchd` scheduling.
- Flair Client ID and Client Secret from Flair Developer Settings.
- Internet access for Flair and Open-Meteo; background service requires the Mac
  to **remain awake** (its display can sleep).

## Authoritative releases and installing updates

Release ZIPs are **full snapshots of the source tree**. Extract them into the
repository root with **overwrite** enabled. Keep these local paths intact:

- `.env` (credentials), `.venv/` (per-computer Python environment)
- `data/` (SQLite history), `logs/` (runtime logs)
- `.git/` (repository and version history)

The ZIP contains no `.env`, local databases, or logs. `setup.sh` also removes
specific obsolete preview files (`controller_next.py`, `README-NEXT.md`, and
root-level `test_climate_policy.py`/`test_control_store.py`). It never removes
personal runtime data.

From the root of your existing clone:

```bash
cd ~/flair-automation
unzip -oq ~/Downloads/bedroom-climate-current.zip -d .
chmod +x setup.sh
./setup.sh --test
```

`unzip -o` **overwrites project source files**. Any local modifications to
those files will be lost; Git can restore previous commits when necessary.

## First-time local setup

```bash
chmod +x setup.sh
./setup.sh
```

If needed, the setup script creates `.env` from `.env.example`. Put your actual
credentials into `.env` **locally**, never in chat or Git:

```dotenv
FLAIR_CLIENT_ID=your-client-id
FLAIR_CLIENT_SECRET=your-client-secret
WEATHER_LOCATION=Indianapolis
```

The setup script creates/updates `.venv`, installs dependencies, and creates
local `data/` and `logs/` directories. A subprocess cannot activate your
parent Terminal session. To activate manually:

```bash
source .venv/bin/activate
```

## Development/testing

```bash
./setup.sh --test                # Offline unit tests
./setup.sh --run-once            # Live APIs, record SQLite readings/decisions
python controller.py --at 23:00 --previous off
python controller.py --at 12:00 --previous intake2
python test_flair.py             # Live API smoke test
python test_weather.py           # Live weather smoke test
```

The `--at` simulation uses live sensor data but simulates local time; it never
writes to SQLite. `--previous` can only be used with `--at`. Without `--at`,
the controller records observations and decisions to `data/bedroom.sqlite3`.

## SQLite history

- `sensor_readings`: actual received readings at the time the script runs;
  UTC-recorded time plus source-provided timestamps when available.
- `control_decisions`: chosen mode/speed, prior **desired** mode/speed, reason,
  schedule status, and `command_sent=0` for dry runs.

The original Flair room `state-updated-at` field has **not** been proven to be
the time of the last Puck measurement. Do not assume this field proves data
freshness. A failed API request stops that run rather than sending commands.

To inspect recent history:

```bash
python - <<'PY'
import sqlite3
with sqlite3.connect('data/bedroom.sqlite3') as db:
    for row in db.execute('''
        SELECT recorded_at, bedroom_temp_f, outdoor_temp_f
        FROM sensor_readings ORDER BY id DESC LIMIT 5
    '''):
        print(row)
PY
```

## Optional macOS background collector (later, on studio Mac mini)

```bash
./setup.sh --install-launchd   # Collects every 5 minutes while Mac is awake
./setup.sh --status
./setup.sh --uninstall-launchd
```

`launchd` calls `controller.py` using the `.venv` Python interpreter; no
Terminal window or shell activation is required. This is a **user LaunchAgent**,
so it requires a signed-in user session. It is **not installed automatically**
by `./setup.sh`.

## Git workflow

After extracting a release and running tests:

```bash
git status --short
git add -A
git diff --cached --name-only  # Should NOT show .env, data/, logs/, .venv/
git commit -m "Update bedroom climate controller"
git push
```

If secrets were ever previously committed, rotate them; adding `.gitignore`
alone does not remove already tracked files from Git history.

## Source organization

```text
controller.py          # Canonical dry-run app entry point
climate_policy.py      # Pure fan schedule/cooling/speed logic
check_cooling.py       # Flair room + Open-Meteo readings
control_store.py       # SQLite desired-state/decision history
database.py            # SQLite sensor history
test_flair.py          # Manual live Flair smoke test
test_weather.py        # Manual live weather smoke test
tests/                 # Offline regression tests
setup.sh               # Portable setup + optional launchd integration
.env.example           # Local credential template (no secrets)
requirements.txt       # Python dependencies
```

## Planned integrations

1. BroadLink RM4 Pro: local Python IR control of Vornado, and RF control of
   ceiling fan **only if its unknown RF protocol proves compatible**.
2. Shelly Plug US Gen4: keep energized; use **power monitoring only**, never
   outlet switching to control Vornado power.
3. Diagnostics: detect unexpected cooling/warming outcomes and flag suspected
   missed IR commands, reversed direction, excess infiltration, etc.
4. Experimental exhaust-assisted warming with hallway/Nest temperature; test
   effectiveness and combustion-appliance backdraft risk before enabling.

The controller currently runs only dry-run decisions, and none of the above
planned device integrations should be treated as deployed or verified.

# Bedroom climate automation

Python controller for Flair bedroom readings, Open-Meteo outside weather, and the
Vornado Transom. **Current version is dry-run only:** no BroadLink commands,
Shelly switching, or physical fan control. The Flair Smart Vent remains
independent. Every normal run stores a detailed audit trail in local SQLite.

## Full-release upgrade

Each ZIP is the **entire authoritative project**. Overwrite the source files:

```bash
cd ~/flair-automation
unzip -oq ~/Downloads/bedroom-climate-current.zip -d .
chmod +x setup.sh
./setup.sh --test
```

`setup.sh` deletes explicitly known obsolete preview files (`control_store.py`,
`controller_next.py`, `README-NEXT.md`, old standalone tests), but never deletes
`.env`, `.venv/`, `data/`, `logs/`, or `.git/`. Neither secrets nor runtime data
are included in the ZIP. Existing SQLite tables/rows are migrated in place.

## `.env` climate controls

On update, `setup.sh` will append any missing `CLIMATE_*` defaults to your
existing `.env` without changing your credential values or other settings.
Editing those non-secret values on your Mac is all you need to change policy.
Every scheduled invocation reloads `.env` settings. **OS environment values
have precedence** over `.env`.

The fully commented, authoritative template is in [`.env.example`](.env.example).
It follows the project's convention: each explanation appears **above** its
variable. Current defaults are target **58°F**, restart **62°F**, speeds 2–4
at **64/68/72°F**, 5°F outdoor advantage, and 22:00–07:30 Indianapolis time.


The target is when the fan should stop cooling. Cooling restarts when the
bedroom reaches the restart threshold (if outside air is sufficiently cooler).
Between those thresholds, the previous *desired* state determines whether
cooling continues. Speed 1 is favored for quiet; configurable speed thresholds
and hysteresis control speeds 2–4. All temperatures are Fahrenheit. Scheduling
handles overnight windows and Indianapolis daylight saving time. Invalid
settings fail safely and get logged, rather than issuing hardware commands.

## Offline hardware command planning (new)

Before the RM4 Pro and Shelly Plug arrive, test the *next command* that the
controller would eventually request. This is entirely fake: there are no
network calls, live devices, or SQLite writes in the hardware simulator.

```bash
# Example: fan consumes simulated standby wattage, so plan power-on toggle
.venv/bin/python simulate_hardware.py --at 23:00 --bedroom 66 --outside 40 --watts 1

# Example: running on speed 1, desired speed 2; plan ONE speed-up command
.venv/bin/python simulate_hardware.py --at 23:00 --bedroom 66 --outside 40 --watts 11 --direction intake --speed 1

# Example: no power measurement; refuse unsafe toggle
.venv/bin/python simulate_hardware.py --at 23:00 --bedroom 66 --outside 40

# Example: schedule ended; running fan should be turned off
.venv/bin/python simulate_hardware.py --at 12:00 --bedroom 66 --outside 40 --watts 11
```

**Important safety semantics:**

- The default simulated thresholds (≤2W standby, ≥8W running) are **made-up
  examples for testing**, NOT real readings or validated Shelly calibration.
- Shelly is a **read-only power monitor** in this project; no code to switch
  the Vornado's power outlet is included.
- The Vornado's Power and Direction buttons are **toggles**; IR success is
  not confirmed just because a signal was sent. Do not retry blindly.
- Power metering cannot prove direction or speed. Unknown direction/speed
  blocks incremental commands until a separate calibration procedure exists.
- The planner proposes **one command per observation**. A future runtime
  must capture the new state (or establish a safe recovery method) before
  deciding what to transmit next.
- The simulator's cooling policy honors your `.env` values; the displayed
  `fake_commands` list is **not transmitted** anywhere.
- Ceiling-fan RF compatibility with BroadLink remains unconfirmed; the
  ceiling-fan commands are intentionally not integrated into this plan yet.

Components: `device_plan.py` (pure reconciliation), `devices/interfaces.py`
(mockable IR and wattage interfaces), and `simulate_hardware.py` (CLI).

## Run and test locally

Requires Python 3.10+, Git, and network access for actual Flair/weather calls.

```bash
./setup.sh                 # Install Python virtual environment and dependencies
./setup.sh --test          # Run offline regression tests
./setup.sh --run-once      # Query real APIs and append SQLite history, no IR
source .venv/bin/activate  # Optional, for running Python interactively
python controller.py --at 23:00 --previous off  # Simulated time; no DB writes
python history.py --decisions 20
python history.py --decisions 0 --configs
python history.py --decisions 0 --failures
```

You do not need to activate the virtual environment when using `./setup.sh`;
it explicitly uses the `.venv` interpreter.

## SQLite historical tracking

Database: `data/bedroom.sqlite3`. Recorded times are UTC; original sensor
source timestamps are retained as supplied. The old Flair `state-updated-at`
field is **not validated as the latest Puck measurement time**.

| Table | What gets recorded |
| --- | --- |
| `sensor_readings` | Indoor/outdoor temperatures, humidity, dew point and source timestamps for each successful evaluation |
| `control_decisions` | **Every successful evaluation**, even when state doesn't change; previous/desired state, reason, reason code, schedule status, target/restart/minimum advantage, full JSON inputs and effective settings; `command_sent=0` |
| `config_snapshots` | Every distinct complete **non-secret** effective climate/weather configuration |
| `config_activations` | When a config first takes effect or changes (including reverts), with structured JSON before/after differences |
| `controller_runs` | Every normal attempt, success or error, with start/finish timestamp and failure stage/type |

Each decision links to its configuration snapshot and controller run. The
setting snapshots exclude credentials. Prior SQLite records remain intact;
new columns on historical rows may be `NULL` because those values were not
originally captured. Runs with API errors log the failure but do not create a
fabricated decision.

You can inspect the most recent decisions:

```bash
python history.py --decisions 10 --configs --failures
```

Or query the values active when a specific decision was made:

```bash
python - <<'PY'
import sqlite3
with sqlite3.connect('data/bedroom.sqlite3') as db:
    for row in db.execute('''
        SELECT recorded_at_utc, bedroom_temp_f, outdoor_temp_f,
               target_f, restart_f, desired_mode, desired_speed, reason
        FROM control_decisions ORDER BY id DESC LIMIT 10
    '''):
        print(row)
PY
```

**Desired** fan state is not verified physical state. A separate offline hardware command planner exists, but it is not yet called
from the live controller. After the BroadLink and Shelly arrive, we will add
validated local APIs, wattage calibration, safe command execution, actual
command logging, fault detection, and experimental exhaust-assisted warming.

## Optional launchd on the studio Mac mini (later)

```bash
./setup.sh --install-launchd   # Every five minutes, all day
./setup.sh --status
./setup.sh --uninstall-launchd
```

The Mac must remain **awake**, although the screen may sleep. This user
LaunchAgent runs while a user session is signed in. It is never enabled by
setup unless you explicitly request `--install-launchd`.

## Git workflow

```bash
git add -A
git diff --cached --name-only # Confirm .env, data/, logs/, .venv/ are absent
git commit -m 'Add offline device planning and update climate defaults'
git push
```

## Canonical application modules

- `controller.py`: single entry point, dry run
- `settings.py`: validates `.env` climate settings
- `climate_policy.py`: pure schedule/temperature/speed policy
- `check_cooling.py`: Flair + Open-Meteo data acquisition
- `database.py`: schema migration and comprehensive event/decision history
- `history.py`: read-only SQLite reports
- `device_plan.py`: safe, single-command power/speed/direction planning
- `devices/interfaces.py`: fake BroadLink and read-only fake Shelly interfaces
- `simulate_hardware.py`: offline IR/wattage scenario runner
- `tests/`: offline unit and mocked integration tests
- `setup.sh`: dependencies, test runner, optional launchd installer

# Bedroom climate automation

Python controller for Flair bedroom readings, Open-Meteo outside weather, and the
Vornado Transom. **The actual climate controller is still dry-run only.**
This release includes **opt-in, read-only** Shelly local RPC, a guarded
BroadLink connection/IR-codebook adapter, and a pure simulated safety state
machine. **Neither physical adapter is called by the controller.**
The Flair Smart Vent remains independent. Every normal controller run stores a
detailed audit trail in local SQLite.

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
variable. Current defaults are target **60°F**, restart **64°F**, speeds 2–4
at **62/64/68°F**, 5°F outdoor advantage, and 22:00–07:30 Indianapolis time.


The target is when the fan should stop cooling. Cooling restarts when the
bedroom reaches the restart threshold (if outside air is sufficiently cooler).
Between those thresholds, the previous *desired* state determines whether
cooling continues. Speed 1 is the default while cooling below 62°F; at or
below the 60°F target the fan is OFF. Configurable speed thresholds (62°F,
64°F, 68°F for speeds 2–4) and hysteresis control speed transitions. All temperatures are Fahrenheit. Scheduling
handles overnight windows and Indianapolis daylight saving time. Invalid
settings fail safely and get logged, rather than issuing hardware commands.

## New: state reconciliation and diagnostics (offline)

The new `safety_state.py` and `temperature_diagnostics.py` modules are pure
Python and send **no IR/RF commands**. The controller is still dry run.

A state snapshot explicitly separates:

- **Desired**: cooling policy's requested mode/speed.
- **Commanded**: a command actually attempted, or `NULL` when nothing has
  been sent. In this release, command attempts can only be saved as
  `simulated` and are never physically transmitted.
- **Observed**: a fresh, calibrated Shelly power reading and (only if
  independently verified) direction and speed. In normal dry-run controller
  records, these remain `NULL`.

The safety engine allows **at most one next proposed command**. After a power
IR toggle it demands a **later** wattage observation to confirm that the fan's
running state changed. If there is no confirmation, it *blocks*, logs a
fault in simulated scenarios, and will **never blindly retry the toggle**.
Direction and speed cannot be confirmed by wattage: the initial implementation
requires manual verification after such commands. This restriction can be
refined after Tuesday's hardware experiments, but not relaxed by guessing.

Thermal anomaly rules are advisory, not automatic correction. They require a
window of 3+ measurements and trusted physical-state evidence. They may flag
rapid cooling with the fan off, warming during verified intake, or cooling
during verified exhaust while the hallway is warmer. A temperature trend
**cannot prove reversed airflow**, so these rules do not automatically toggle
fan direction. Hallway temperatures and verified fan direction are supplied by
fake samples for now; the live sensor pipeline does not yet supply both.

To run all four deterministic, independent hypothetical cases without any
network requests, hardware interaction, or production database changes:

```bash
.venv/bin/python simulate_faults.py
```

This script exercises: a successful power toggle, a missed IR toggle, stale
Shelly readings, and suspicious exhaust-temperature trends. It records fake
events to a **temporary SQLite database**, prints a summary and deletes the
temporary database when finished. Wattage values are intentionally fictional.

Each normal live dry-run controller call now adds a `device_state_snapshots`
row with its desired state and `physical_state_unverified`; it never invents an
observed state or an IR attempt. Failed API calls are also appended as sanitized
`diagnostic_events` (only failure stage/type, never tokens or full exception text).

After running `./setup.sh --run-once`, inspect the new history tables:

```bash
.venv/bin/python history.py --decisions 0 --states 10
.venv/bin/python history.py --decisions 0 --diagnostics 10
.venv/bin/python history.py --decisions 0 --commands 10
```

The real controller **does not** yet use the simulated reconciliation engine,
and the pending-command logic has not been wired into an unattended runtime.
That is intentional: physical power thresholds and IR transmission behavior
are still unknown. Existing tables and historical rows are retained by
additive SQLite migrations.

## Offline hardware command planning

Before the RM4 Pro and Shelly Plug arrive, test the *next command* that the
controller would eventually request. This is entirely fake: there are no
network calls, live devices, or SQLite writes in the hardware simulator.

```bash
# Example: fan consumes simulated standby wattage, so plan power-on toggle
.venv/bin/python simulate_hardware.py --at 23:00 --bedroom 66 --outside 40 --watts 1

# Example: running on speed 1, desired speed 3; plan ONE speed-up command toward it
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

## Local device adapters (new, safe without hardware)

Included modules:

- `devices/shelly_local.py`: Shelly Plug US Gen4. **HTTP GET ONLY** of
  `/rpc/Switch.GetStatus?id=0`. Parses actual watts (`apower`), relay status,
  voltage and current. **No power on/off API exists** in the adapter.
  If the plug relay is OFF, its `read_watts()` refuses to treat this as the fan
  being off; manually restore power using the Shelly app if needed.
- `devices/broadlink_local.py`: local BroadLink RM4 Pro targeted discovery,
  authentication and learned IR/RF code loading. Transmission requires a
  caller to explicitly set `permit_transmit=True`. The application does not
  set this or invoke `send()`.
- `hardware_probe.py`: intentional *manual* local device connectivity checks,
  no IR/RF transmission and no Shelly relay switching. Records successful
  probes and failed attempts to SQLite `hardware_observations` (error type only).
- `config/remote_codes.example.json`: logical command slots, currently **all
  empty**. Learned signal packets must be captured from the actual remotes;
  no fake codes are supplied. Future local codebook path:
  `data/remote_codes.json` (local and Git-ignored).

**Nothing to configure until the products arrive.** On Tuesday, find the
LAN IP of each device and set `SHELLY_HOST` / `BROADLINK_HOST` in `.env`.
Then run these **manual** checks separately:

```bash
# No network requests or device changes, just show currently saved command names
.venv/bin/python hardware_probe.py --codes

# Connect and read watts/relay from Shelly; no relay changes
.venv/bin/python hardware_probe.py --shelly

# Authenticate to the BroadLink locally; NO IR/RF signals sent
.venv/bin/python hardware_probe.py --broadlink

# View recently stored status/probe records
.venv/bin/python history.py --decisions 0 --hardware 15
```

The BroadLink must be configured on 2.4 GHz Wi-Fi and may require disabling
"Lock device" in the BroadLink app to permit third-party local access. Both
computer and devices must be reachable on the same LAN. RF compatibility
with the ceiling-fan remote is still *untested*. The exact Vornado codes,
physical fan behavior on power-up, direction toggling, and Shelly wattage
thresholds still need empirical testing. **This release does not learn or send
live codes yet.**

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
| `hardware_observations` | Every explicit live Shelly/BroadLink probe: time, device, measurement metadata or error type; no credentials or packet codes |
| `device_state_snapshots` | Desired vs last commanded vs verified observed state; a row for every normal dry-run evaluation, plus simulated state transitions |
| `command_attempts` | Simulated logical commands only; `sent` is prohibited until real hardware verification |
| `diagnostic_events` | Sanitized API failures and anomalies produced during offline simulations |

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

**Desired** fan state is not verified physical state. The offline command planner
and real-device adapters are separate and NOT called by `controller.py`.
After the BroadLink and Shelly arrive, we must calibrate wattage, capture
remote packets, validate command/replay behavior, and connect the dry-run
reconciler to durable command delivery and fresh observations before enabling
live automation. The advisory diagnostics exist in the simulated engine but
are not yet connected to real fan behavior. Experimental exhaust-assisted warming remains future work.

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
git commit -m 'Add offline safety state machine and diagnostics'
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
- `devices/broadlink_local.py`: local BroadLink connection, guarded send and codebook
- `devices/shelly_local.py`: read-only Shelly status/wattage
- `hardware_probe.py`: opt-in connectivity probes, SQLite status logging
- `simulate_hardware.py`: one-step offline IR/wattage planner
- `safety_state.py`: never-retry-unconfirmed-toggle, fresh-observation state machine
- `temperature_diagnostics.py`: advisory, conservative temperature-trend rules
- `simulate_faults.py`: multiple deterministic scenarios, disposable SQLite history
- `tests/`: offline unit and mocked integration tests
- `setup.sh`: dependencies, test runner, optional launchd installer

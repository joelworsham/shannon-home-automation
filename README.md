# Bedroom climate automation

Python controller for Flair bedroom readings, Open-Meteo outside weather, and the
Vornado Transom. **The controller defaults to dry-run.** Explicit live mode
connects read-only Shelly observations and guarded BroadLink delivery for one
policy action per invocation. Ordinary runs and existing scheduled jobs remain
dry-run, even when the hardware environment setting is enabled.
The Flair Smart Vent remains independent. Every normal controller run stores a
detailed audit trail in local SQLite.

## Wake-aware delivery foundation

Supervised October 20 diagnostics established display wake followed by a
successful power action and a speed-up action with approximately one-second
spacing. The six-second display timeout remains user-reported. See
`docs/PROJECT_LOG.md` for measurements and limitations. The original short
wattage ranges cannot identify speed reliably; use independently verified
speed/direction and calibrated, settled power evidence.

`wake_delivery.py` provides the guarded delivery API used by explicit live
controller runs. Run its completely offline demonstration with:

```bash
.venv/bin/python simulate_delivery.py
```

The delivery API is disabled by default. `load_delivery_settings()` reads the
new settings in `.env.example`; callers must pass those settings to
`WakeAwareDelivery`. Setting `HARDWARE_CONTROL_ENABLED=true` alone **does not
enable the controller**, install scheduling, or send anything. Real delivery
also requires a BroadLink adapter constructed with `permit_transmit=True`.
The live CLI is described below; no unattended live loop is installed.

Delivery requires exclusive control: nobody uses the app or original remote
during a sequence. On a new session, expired estimate, or invalidation, it
waits a full display timeout plus timing margin, rereads physical evidence,
sends the captured `speed_up` as a wake, waits one second, rereads evidence,
and sends at most one planned action. The short speed-up capture demonstrated
wake-only behavior; an arbitrary command is not assumed harmless. Timing
settings reflect tested behavior plus user reports, not hardware guarantees.

After an acknowledged command, the display is only **assumed awake** until
the conservative deadline. Another verified action within that window skips
the wake. Slow observations or logging that consume the awake window block
the action. `invalidate_display()` must be called on known app/manual activity
or loss of exclusive control; external IR activity cannot be discovered from
our command log. After a restart, awake estimates are never reused.

`execute(desired, observe, exclusive_control=True)` accepts the existing
`FanState` and a callback returning fresh `PowerObservation` evidence. It
uses the existing reconciler, so unknown power, speed, or direction blocks
commands that need them. The callback owns power calibration and settling;
this API does not classify speed from watts or assume a successful IR send
changed anything. Physical observations must be quick enough for the awake
window. Do not fabricate timestamps or repeat old speed/direction guesses.

Before any send, a durable attempt is reserved in SQLite. A macOS/Linux file
lock excludes simultaneous execution and manual resolution. Its unresolved
status blocks other instances and survives crashes. Acknowledgements and
display estimates are stored separately from physical confirmation.
`confirm_power(attempt_id, observation)` requires a later, fresh, matching
power observation. Speed/direction verification and uncertain/interrupted
attempts require explicit `resolve_manually(..., note=...)` with new physical
evidence. Manual resolution invalidates the awake estimate and does not retry.
Keep notes free of credentials and private device addresses.

`delivery_attempts` and `delivery_events` are additive tables in the existing
SQLite database; older history and the simulated-only `command_attempts`
table are preserved. Missing acknowledgement or logging failure leaves the
attempt unresolved. BroadLink IR delivery now uses a single UDP send: a lost
acknowledgement never triggers a transport retry. A captured packet can still
contain repeated IR frames; one UDP send does not prove one physical action.

The new transport and delivery layer have offline regression coverage. They
have **not been exercised against hardware since implementation**; the earlier
physical diagnostics used ad hoc scripts. Deployment remains separate work.

## One guarded live evaluation

Configure `SHELLY_HOST`, `BROADLINK_HOST`, the captured codebook, and explicit
power thresholds in local `.env`. `FAN_STANDBY_MAX_W` and `FAN_RUNNING_MIN_W`
are intentionally blank in the template: the standby ceiling must be below
the running floor; readings in the gap stay unknown. Choose conservative
thresholds from the calibration record, not the narrow per-speed ranges.
Also set `HARDWARE_CONTROL_ENABLED=true` when intentionally enabling live use.
This documentation does not change your local settings.

```bash
# One real climate evaluation and at most one policy action, plus wake if needed.
# --exclusive-control asserts nobody uses the app/remote during this run.
.venv/bin/python controller.py --live --exclusive-control
```

The policy still honors schedule, outdoor advantage, temperature hysteresis,
and optional speed tiers. This is not an arbitrary command-replay CLI. It can
start/stop based on verified power; a running fan needs independently verified
direction/speed before the planner can adjust those attributes. Supply current
physical observations only when you actually inspected them, for example:

```bash
# Example observations only: replace with the actual state you just inspected.
.venv/bin/python controller.py --live --exclusive-control --verified-direction intake --verified-speed 2
```

These observations expire after 30 seconds by default and are invalidated
after an action. They are not inferred from watts, reused from old chat
messages, or stored as perpetual truth. Do not put fixed verified-state flags
in a scheduler. A power-on confirmation does not establish restored fan speed
or direction, and a single run does not automatically complete every step
toward the desired state.

`power_observer.py` logs every Shelly reading and requires at least three
consistent classifications spanning two seconds by default. Relay OFF,
transition mixtures, threshold-gap readings, or invalid/stale timestamps mean
unknown or failure, not standby. After a command, it discards prior samples,
waits six seconds by default, then collects new evidence. A prior pending
power action may be confirmed on a later invocation, but that run sends no new
command. Speed/direction actions remain pending until manual verification.

Review durable attempts and resolve a specific attempt after inspection:

```bash
.venv/bin/python history.py --decisions 0 --deliveries 10
# Copy the actual attempt ID. This recovery command reads Shelly but never sends IR.
.venv/bin/python hardware_runtime.py --resolve-attempt ATTEMPT_ID --note "Inspected fan; display shows speed 2" --verified-speed 2
```

Recovery requires explicit configured power thresholds and fresh consistent
power evidence. Speed-command recovery requires `--verified-speed`; direction
recovery requires `--verified-direction`. Resolve based on what actually
happened, even if the command failed. The controller will reevaluate from new
evidence; recovery itself never retries a command.

Live runs link delivery attempts to controller run IDs. In decision history,
`command_sent=1` means at least one acknowledged packet, `0` means no recorded
send attempt, and `-1` means pending/uncertain transmission. None proves the
fan responded. Physical confirmations and uncertain outcomes are recorded
separately. Legacy dry-run history stays intact.

`--live` cannot be combined with `--at`. No flags still means dry-run.

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

On update, `setup.sh` will append missing required `CLIMATE_*` defaults
but **not** absent optional speed thresholds to your existing `.env`. It never
changes your credential values or user-entered settings.
Editing those non-secret values on your Mac is all you need to change policy.
Every scheduled invocation reloads `.env` settings. **OS environment values
have precedence** over `.env`.

The fully commented, authoritative template is in [`.env.example`](.env.example).
It follows the project's convention: each explanation appears **above** its
variable. The supplied `.env.example` profile has target **60°F**, restart
**64°F**, speed 2/3/4 thresholds at **62/64/68°F**, 5°F outdoor advantage,
and 22:00–07:30 Indianapolis time.


The target is when the fan should stop cooling. Cooling restarts when the
bedroom reaches the restart threshold (if outside air is sufficiently cooler).
Between those thresholds, the previous *desired* state determines whether
cooling continues. Speed 1 is the fallback; each of speeds 2–4 is optional.
Leave its `.env` value empty or remove its line to disable that tier. The
policy skips disabled tiers when changing speed and still honors hysteresis.
All temperatures are Fahrenheit. Scheduling
handles overnight windows and Indianapolis daylight saving time. Invalid
settings fail safely and get logged, rather than issuing hardware commands.

### Optional fan-speed profiles

The following are alternative settings for your machine-local `.env`:

```dotenv
# Four-speed profile (the supplied .env.example)
CLIMATE_SPEED_2_AT_F=62
CLIMATE_SPEED_3_AT_F=64
CLIMATE_SPEED_4_AT_F=68
```

```dotenv
# Full speed whenever cooling, until the 60°F cooling target is reached
CLIMATE_SPEED_2_AT_F=
CLIMATE_SPEED_3_AT_F=
CLIMATE_SPEED_4_AT_F=60
```

Any subset of speeds 2–4 may be enabled, such as only speed 3.
A missing threshold is disabled, including after running `setup.sh`.
Speed 1 requires no setting and is the fallback only when higher configured
speeds do not qualify. In the full-speed example, speed 4 qualifies at every
temperature above the cooling target, so speed 1 is never requested.
Enabled thresholds must increase in temperature as speed increases.
The 60°F stop / 64°F restart and outdoor-air requirement still apply.
On restart the policy selects the highest qualifying speed; once cooling,
it changes one *enabled* tier per evaluation with hysteresis.

## New: state reconciliation and diagnostics (offline)

The new `safety_state.py` and `temperature_diagnostics.py` modules are pure
Python and send **no IR/RF commands**. Normal controller runs remain dry-run;
explicit live runs use reconciliation through the guarded runtime above.

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

The explicitly enabled live controller uses reconciliation and durable delivery
barriers; the offline fault scenarios remain simulations. Existing tables and
historical rows are retained by additive SQLite migrations.

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
  caller to explicitly set `permit_transmit=True`. Only the explicitly enabled
  live controller path sets this; normal dry-run runs never call it.
- `hardware_probe.py`: intentional *manual* local device connectivity checks,
  no IR/RF transmission and no Shelly relay switching. Records successful
  probes and failed attempts to SQLite `hardware_observations` (error type only).
- `config/remote_codes.example.json`: logical command slots, currently **all
  empty**. Learned signal packets must be captured from the actual remotes;
  no fake codes are supplied. Future local codebook path:
  `data/remote_codes.json` (local and Git-ignored).

Configure the LAN host of each device using `SHELLY_HOST` / `BROADLINK_HOST`
in `.env`. Both devices have passed local connectivity diagnostics on the
development machine; other installations must perform their own checks.
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
thresholds need validation for each installation. The bundled probe never
learns or sends codes. Original-remote captures and supervised replay results
from this installation are documented in the project log; raw codes stay local.

## GitHub Actions checks

[The CI workflow](.github/workflows/tests.yml) runs on pull requests targeting
`main`, pushes to `main`, and manual workflow dispatch. Its single `tests`
check installs the project's dependencies on a clean Linux/Python 3.11
runner, checks `setup.sh` syntax, and runs the complete offline regression
suite with:

```bash
python -m unittest discover -s tests -v
```

Manual network/device probes (`test_flair.py` and `test_weather.py`) are not
included: CI has no local devices or Flair credentials, and it never sends
IR/RF signals.

**Important:** GitHub Actions shows passing or failing checks but does not
itself prevent a merge. To enforce this policy, configure a `main` branch
ruleset/branch protection in GitHub Settings: require a pull request, require
the status check named **`tests`** to pass, and prevent bypasses if desired.
Also require the branch to be up to date before merging to test the current
base. This protection is separate from the workflow file.

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
| `control_decisions` | Every saved policy evaluation, previous/desired state and complete configuration context; dry-run has `command_sent=0`, live uses the delivery accounting described above |
| `config_snapshots` | Every distinct complete **non-secret** effective climate/weather configuration |
| `config_activations` | When a config first takes effect or changes (including reverts), with structured JSON before/after differences |
| `controller_runs` | Every normal attempt, success or error, with start/finish timestamp and failure stage/type |
| `hardware_observations` | Every explicit live Shelly/BroadLink probe: time, device, measurement metadata or error type; no credentials or packet codes |
| `device_state_snapshots` | Desired vs last commanded vs verified observed state; a row for every normal dry-run evaluation, plus simulated state transitions |
| `command_attempts` | Simulated logical commands only; `sent` is prohibited until real hardware verification |
| `diagnostic_events` | Sanitized API failures and anomalies produced during offline simulations |
| `delivery_attempts` / `delivery_events` | Live delivery intent, acknowledgements, uncertainty, display estimates, and physical resolution, linked to controller run IDs |

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

**Desired** fan state is not verified physical state. Explicit live mode connects
the planner and adapters through guarded delivery; ordinary runs stay dry-run.
Power calibration, fresh physical evidence, and exclusive control are required
before live use. The advisory diagnostics exist in the simulated engine but
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

- `controller.py`: dry-run default, explicit one-action live mode
- `hardware_runtime.py`: hardware integration and no-transmission manual recovery
- `power_observer.py`: settled power windows and expiring manual observations
- `wake_delivery.py` / `delivery_store.py`: wake timing and durable delivery barriers
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

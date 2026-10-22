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
The manual live CLI and separately commissioned unattended mode are described below.

Delivery requires exclusive control: nobody uses the app or original remote
during a sequence. On a new session, expired estimate, or invalidation, it
waits a full display timeout plus timing margin, rereads physical evidence,
sends the captured `speed_up` as a wake, waits one second, rereads evidence,
and sends at most one planned action. A low/high action sends four
`speed_down`/`speed_up` presses with the same one-second spacing, refreshing
the awake estimate conservatively from each send start. The wake press is separate.
The short speed-up capture demonstrated
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
uses the reconciler: unknown power or direction blocks commands that need
them. Unknown speed can be replaced with a low/high endpoint sequence.
The callback owns power calibration and settling;
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

The transport and delivery layer have offline regression coverage. On October 21,
supervised integrated startup and both four-press endpoint sequences succeeded
on this Mac; the user confirmed intake and the displayed endpoints. See the
project log for the distinction between physical evidence and modeled state.

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
and low/high speed selection. This is not an arbitrary command-replay CLI. It can
start/stop based on verified power; a running fan needs independently verified
direction before the planner can adjust speed. Starting speed may be unknown. Supply current
physical observations only when you actually inspected them, for example:

```bash
# Example observations only: replace with the actual state you just inspected.
.venv/bin/python controller.py --live --exclusive-control --verified-direction intake --verified-speed low
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
.venv/bin/python hardware_runtime.py --resolve-attempt ATTEMPT_ID --note "Inspected fan; display shows low" --verified-speed low
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

## Commissioned unattended intake control

`controller.py --unattended` is a separate, explicit mode for a fan reserved
exclusively for automation. It requires `HARDWARE_CONTROL_ENABLED=true`,
`UNATTENDED_CONTROL_ENABLED=true`, and local commissioning in
`data/fan_commissioning.json`. The normal `--live` path retains manual
speed/direction verification; it does not inherit the commissioned assumptions.

Commissioning records the operator's intake-only contract and a physically
verified initial endpoint. The system cannot sense direction, discover all
manual remote activity, or guarantee direction survives a fan power outage.
Pause automation before manual use or disconnecting/replacing devices, then
inspect intake and recommission before resuming. A change to hardware hosts,
the codebook, or power-observation settings invalidates commissioning.

For later speed changes, a modeled endpoint requires all four intended action
presses acknowledged, a complete durable sequence, and a settled window whose
every power sample agrees with that endpoint's calibrated band. This is
**command/model evidence with a power consistency check**, not direct physical
speed verification: a missed press ending at an intermediate speed with
overlapping watts can escape this check. Watts alone never select a speed.
SQLite physical `verified_speed`/`verified_direction` columns stay empty for
modeled state; separate model records explain its basis.

After a power action, restored speed is unknown and a later invocation establishes
the requested endpoint. At most one policy action occurs per invocation.
Incomplete/uncertain delivery and pending endpoint attempts after a restart
require manual inspection, never automatic retry. An endpoint-model mismatch
latches `data/fan_commissioning_fault.json` until inspection/recommissioning;
verified power-off remains possible when the policy requests off, unless a
delivery is unresolved. Existing manual recovery is still available.

Commission only after physically verifying intake, low/high endpoint behavior,
power restoration behavior, and settled per-installation calibration. Example
command shape (supply your measured bands; these numbers are not universal):

```bash
.venv/bin/python unattended.py --exclusive-control --verified-direction intake \
  --verified-speed high --low-band LOW_MIN_W LOW_MAX_W --high-band HIGH_MIN_W HIGH_MAX_W
# Set both opt-ins in .env, then install the live schedule:
./setup.sh --install-launchd-live
```

To pause fan control while retaining collection, run `./setup.sh --install-launchd`.
To stop the job entirely, run `./setup.sh --uninstall-launchd`. Before resuming
after manual use, physically establish intake at a supported endpoint, rerun
commissioning with that actual endpoint, then reinstall with `--install-launchd-live`.
Preserve `.env`, `data/` (including SQLite, codebook, and commissioning), and logs
when updating the source.

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

## Planned extensions

See [the feature tracker](docs/FEATURE_TRACKER.md) for the scope, proposed behavior,
implementation order, and acceptance checks for sleep/daytime presets, temperature
and OFF holds, manual pause/resume, and a private Android remote. These extensions
are scoped only; they are not implemented or enabled.

## `.env` climate controls

On update, `setup.sh` will append missing required `CLIMATE_*` defaults
but **not** an absent optional high-speed threshold to your existing `.env`. It never
changes your credential values or user-entered settings.
Editing those non-secret values on your Mac is all you need to change policy.
Every scheduled invocation reloads `.env` settings. **OS environment values
have precedence** over `.env`.

The fully commented, authoritative template is in [`.env.example`](.env.example).
It supplies a 60°F stop target, 64°F restart, 5°F outdoor advantage, and a
22:00–07:30 Indianapolis schedule. All temperatures are Fahrenheit.

The fan has only two supported running states: **low** (physical speed 1) and
**high** (physical speed 4). Speeds 2 and 3 are neither policy targets nor
accepted observations. Physical transitions can pass through them.

`CLIMATE_HIGH_AT_F=68` selects high when starting cooling at or above 68°F.
While cooling, `CLIMATE_SPEED_HYSTERESIS_F=0.5` switches low → high at 68.5°F
and high → low below 67.5°F. Blank or absent `CLIMATE_HIGH_AT_F` means always
low. Set it to the cooling target to use high whenever cooling is active.
The stop/restart thresholds and outdoor-air requirement always apply.

Low/high requests use endpoint actions rather than relative adjustments:
`set_low` sends four speed-down presses; `set_high` sends four speed-up presses.
Starting speed is unnecessary. A separately verified matching endpoint skips
the sequence. The wake press is additional, and each action press is spaced
and journaled individually. An interrupted sequence remains unresolved for
inspection. Acknowledged delivery does not prove reception, so speed actions
still require physical verification afterward; automatic endpoint wattage
classification is not enabled by this refactor. The four-press endpoint
behavior is covered by simulations and has not been exercised live here.

CLI observations use `--verified-speed low` or `--verified-speed high`; simulated
previous states use `--previous intake-low` or `--previous intake-high`.
Internal speed values and SQLite snapshots retain physical endpoint numbers
1/4 (0 for off). There are no legacy setting aliases or conversion migrations.

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

# Example: running high, desired low; plan four speed-down presses
.venv/bin/python simulate_hardware.py --at 23:00 --bedroom 66 --outside 40 --watts 30 --direction intake --speed high

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
- Power metering cannot prove direction; speed classification is not enabled.
  Unknown direction blocks speed changes. Unknown speed allows an endpoint action.
- The planner proposes **one logical action per observation** (four presses for speed).
  The live runtime requires confirmation or manual recovery before another action.
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

## Persistent macOS server

This Mac is the persistent server. Keep the project in its installed folder;
the LaunchAgent uses absolute paths. If you move the project, rerun the installer
from the new folder to update the interpreter, working directory, and log paths.

```bash
./setup.sh --install-launchd        # Dry-run collection at the configured interval
./setup.sh --install-launchd-live   # Commissioned live control at the same interval
./setup.sh --status
./setup.sh --uninstall-launchd
```

`COLLECTOR_INTERVAL_SECONDS` in `.env` sets the background collection interval
in seconds (default `300`). Use a positive whole number. After changing it, run
the appropriate install command above to reload the macOS schedule. Other `.env`
settings are read on each run.

The Mac must remain **awake**, although the screen may sleep. This user
LaunchAgent runs while a user session is signed in. It is never enabled by
setup unless you explicitly request one of the launchd installation options.

After a reboot, sign in to start collection again. Locking the screen is fine;
logging out stops this user service. Check system sleep with `pmset -g custom`
(AC power `sleep 0` keeps this desktop awake).

`--status` normally reports `state = not running` between invocations. Check
`last exit code = 0`, the current project paths, and advancing run timestamps
in SQLite or `logs/collector.stdout.log`. Errors appear in
`logs/collector.stderr.log`. The collector runs all day; the climate operating
window in `.env` determines when cooling is requested. The installed arguments
in `--status` show whether the job runs collection only or includes `--unattended`.
Hardware enablement alone never changes a dry-run job into live control.

On macOS, Python also needs **Privacy & Security → Local Network** access for
Shelly/BroadLink. A background `No route to host` while an interactive probe
works can indicate this permission is missing. Enable Python there, then retry
the installed job with `launchctl kickstart gui/$(id -u)/local.shannon.bedroom-climate.collector`.
No Full Disk Access is required for this setup.

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
- `unattended.py`: commissioned exclusive intake contract and endpoint model
- `power_observer.py`: settled power windows and expiring manual observations
- `wake_delivery.py` / `delivery_store.py`: wake timing and durable delivery barriers
- `settings.py`: validates `.env` climate settings
- `climate_policy.py`: pure schedule/temperature/speed policy
- `check_cooling.py`: Flair + Open-Meteo data acquisition
- `database.py`: schema migration and comprehensive event/decision history
- `history.py`: read-only SQLite reports
- `device_plan.py`: safe power/direction and low/high endpoint planning
- `devices/interfaces.py`: fake BroadLink and read-only fake Shelly interfaces
- `devices/broadlink_local.py`: local BroadLink connection, guarded send and codebook
- `devices/shelly_local.py`: read-only Shelly status/wattage
- `hardware_probe.py`: opt-in connectivity probes, SQLite status logging
- `simulate_hardware.py`: offline endpoint IR/wattage planner
- `safety_state.py`: never-retry-unconfirmed-toggle, fresh-observation state machine
- `temperature_diagnostics.py`: advisory, conservative temperature-trend rules
- `simulate_faults.py`: multiple deterministic scenarios, disposable SQLite history
- `tests/`: offline unit and mocked integration tests
- `setup.sh`: dependencies, test runner, optional launchd installer

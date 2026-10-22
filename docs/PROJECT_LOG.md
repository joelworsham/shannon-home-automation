# Project log (working document)

> **Purpose:** Temporary, living record of completed work, open questions, and next steps. Use it for project continuity; do not treat it as a replacement for the code, `README.md`, or `.env.example`. Update after meaningful milestones, then archive or replace with GitHub Issues when appropriate.
>
> Last reviewed: **2026-10-21**. Current status includes commissioned unattended intake deployment on this Mac; dated entries below are historical. Statements about hardware are not confirmations until physically verified.

## Durable decisions

- **Architecture:** Python-first, locally hosted where practical; cloud services are acceptable when necessary. Google Home is an existing ecosystem, not a requirement for every device.
- **Source of truth:** Repository code and tests for behavior; `.env.example` for defaults; `README.md` for setup. Never commit `.env`, credentials, learned codebook, SQLite history, or logs.
- **Climate inputs:** Original Flair Puck for bedroom temperature/humidity, Open-Meteo for outdoor conditions. Flair Smart Vent retains its independent control until an intentional integration is made.
- **Fan outputs:** BroadLink RM4 Pro to transmit learned IR (Vornado Transom) and potentially RF (ceiling fan). RF protocol compatibility must be demonstrated.
- **Power observation:** Shelly Plug US Gen4 is for **read-only electrical monitoring**, never the routine means of power-cycling the Vornado. Its relay should remain on.
- **Safety:** Separate *desired*, *commanded*, and *observed* state. IR success cannot be inferred from sending a packet. Do not blindly re-toggle power/direction when state is unknown. Temperature anomalies are evidence, not proof of reversed airflow.
- **History:** SQLite stores measurements, decisions and reasons, config snapshots/changes, device evidence, diagnostic events, and failures. Preserve history across code updates.
- **Deployment:** The current Mac is the persistent server (confirmed 2026-10-21). Its five-minute commissioned unattended intake LaunchAgent is enabled, with system sleep disabled and display sleep allowed. The service requires a signed-in user and must be reinstalled after moving the project. Intake is a commissioned exclusive-control contract, not a sensed direction; pause before manual use and recommission afterward.
- **Code delivery:** Favor direct GitHub changes (branch/PR when useful); otherwise complete canonical release snapshots, without competing `*_next` variants. Preserve machine-specific runtime files.

## Established operating preferences

Defaults in `.env.example` at this review (actual `.env` may override):

| Parameter | Default |
| --- | --- |
| Schedule (Indianapolis local time) | 22:00–07:30 |
| Cooling target / restart | 60°F / 64°F |
| Minimum outdoor cooling advantage | 5°F |
| Running speeds | Low (physical 1) / high (physical 4) |
| Bedroom threshold for high | Optional; example 68°F (`CLIMATE_HIGH_AT_F`) |
| Speed hysteresis | 0.5°F |
| Planned monitoring interval | Five minutes, 24/7 |

Prefer the quietest practical speed. Experimental exhaust-assisted warming should be treated as separate, opt-in functionality rather than assumed to work.

## Historical policy decisions (superseded by low/high control)

**2026-10-19 configuration change:** Repository defaults changed from 58/62°F to 60/64°F (target/restart). Speed thresholds remain 64/68/72°F. Existing machine-local `.env` values are not changed by repository updates; change them explicitly to adopt the new settings.

**2026-10-19 speed threshold change:** Set the four intended speed boundaries to **60/62/64/68°F for speeds 1–4**. The code expresses speed 1 as the default when active below the 62°F speed-2 cutoff; at or below the 60°F cooling target, the fan stops. Default speed 2/3/4 thresholds changed from 64/68/72°F to 62/64/68°F. Cooling restart remains 64°F; hysteresis remains 0.5°F. Machine-local `.env` values must be updated separately if they override defaults.

**2026-10-19 optional-speed pivot:** Speed 2/3/4 thresholds are independently optional. Blank or absent `.env` values disable their tiers; `setup.sh` does not append intentionally omitted speed settings. Speed 1 stays the implicit fallback when cooling is requested but no enabled higher threshold qualifies. With speed 2/3 unset and speed 4 at 60°F, speed 4 is requested for all active cooling until the 60°F stop target. Speed hysteresis must skip disabled tiers and handle previously desired speeds that have been disabled. Existing 60°F stop / 64°F restart / 5°F cooling advantage and dry-run-only safeguards remain unchanged. Configuration history can store disabled thresholds as `null`. This is a tested policy change, **not** validated physical fan control.

**2026-10-19 automated checks:** Added `.github/workflows/tests.yml` to run the complete offline `tests/` suite, dependency installation, and `setup.sh` shell syntax check on each pull request to `main` and each push to `main`. Manual `test_flair.py` / `test_weather.py` probes deliberately stay out of CI to avoid credential or live-device dependency. The expected required check is named **`tests`**. **Enforcement remains a separate GitHub settings task**: require the `tests` check in a `main` ruleset / branch protection, require PRs, and disallow bypass as appropriate. The current GitHub integration cannot edit that repository protection setting. The first pull-request workflow run ([Actions run 39274495353](https://github.com/joelworsham/shannon-home-automation/actions/runs/39274495353)) **passed**, including dependency installation, shell syntax, and offline tests. After merging, verify the first `main` push run as well.

## Current implementation and remaining work

- **Controller:** Configurable schedule, cooling stop/restart, outdoor advantage, and low/high selection. Normal runs and the installed five-minute collection job remain dry-run. Explicit live mode uses guarded delivery.
- **Speed control:** Only low/high are accepted targets or observations. Endpoint actions send four down/up presses with separate display wake when needed. Starting speed can be unknown. No intermediate-speed policy or relative-step planner remains.
- **Evidence:** Raw Shelly monitoring and standby/running classification are integrated. Endpoint calibration trials exist; automatic wattage-based low/high classification is not enabled. Speed/direction actions still require physical verification afterward.
- **Hardware:** Local device connectivity and earlier wake/power/incremental experiments were physically observed as described below. The new four-press endpoint sequences have only offline validation.
- **Remaining:** Supervised endpoint saturation and direction validation before unattended operation; automatic endpoint classification remains future work. No additional four-speed calibration is required by this design. Ceiling-fan RF compatibility remains unverified.

## Historical hardware development record

Dated observations below describe the implementation and hardware evidence at that time.
Intermediate-speed measurements are historical experiments, not supported states or current configuration guidance.

### First physical connectivity probes (2026-10-20)

- **Shelly Plug US Gen4 — local API verified:** User ran `.venv/bin/python hardware_probe.py --shelly` and received `Shelly read-only: 0.00 W; outlet relay ON`. This confirms a successful read-only RPC response and that the plug relay was on at the time. **0 W is not yet a calibrated fan standby measurement**; verify actual Vornado operating and standby wattages later.
- **BroadLink RM4 Pro — local authentication verified:** First `.venv/bin/python hardware_probe.py --broadlink` failed at `device.auth()` with `broadlink.exceptions.AuthenticationError: [Errno -1] Authentication failed` because **Lock Device** was ON in the BroadLink app. User disabled that setting and reran the same probe; it returned **`BroadLink local connection successful: rm4pro (no commands sent)`**. This verifies targeted LAN communication and authentication using the existing Python adapter, **not** IR/RF learning, transmission, or physical fan control. Keep the BroadLink's local-access implications in mind on the trusted home LAN; avoid storing IPs/MACs here.

### Codex handoff verification (2026-10-20)

- Confirmed direct workspace access, an initially clean `main`, and both device host settings populated in local `.env` without disclosing their values. Fast-forwarded the checkout to GitHub `main` at `593d073`; the two incoming commits only updated this log.
- Ran the complete offline unittest suite: **105 tests passed**. Inspected the controller and local adapters: controller remains dry-run, Shelly exposes only status reads, and BroadLink transmission requires explicit opt-in. No live device requests or transmissions were made during this verification.
- Smallest next experiment: user confirms Vornado standby with the outlet relay ON, allows readings to settle, then collects repeated readings using the existing Shelly probe. Repeat for manually selected speeds 1–4 and a second standby pass. Record the user-observed state alongside wattage ranges; retain uncertainty during transitions. Existing probes already persist raw measurements in SQLite. No thresholds or speed-verification claims are established yet.

### First supervised standby samples (2026-10-20)

- User reported Shelly ON and Vornado already OFF for an extended period, from another room; user will operate the BroadLink app for manual calibration changes and can inspect the fan if readings are suspect.
- Collected six actual Shelly status readings with five-second pauses between requests: **all 0.00 W**, relay **ON** throughout (min/max/mean 0.00 W). Saved each timestamped observation to local SQLite with a calibration-run identifier and explicit user-reported standby evidence. This is one standby sample set, not a validated classification threshold or independently observed physical state.
- Initial sandboxed request failed with `ConnectionError`; retried with approved local-network access and completed successfully. Both the failure and successful readings are recorded locally. No IR/RF or relay commands sent.
- Next: user selects speed 1, allows stabilization, and confirms the selected state; collect running readings before inferring standby-versus-running separation. Repeat other speeds and standby afterward.

### First supervised speed-1 samples (2026-10-20)

- User reported fan ON at speed 1 after at least 30 seconds of stabilization, using the BroadLink app from another room.
- Six actual Shelly readings, separated by five-second pauses: **24.40, 24.50, 24.40, 24.50, 24.30, 24.20 W**. Range **24.20–24.50 W**, mean **24.38 W**; relay ON throughout. Timestamped samples and user-reported state evidence saved to local SQLite.
- These readings are clearly separated from the initial 0.00 W standby set. Speed 1 remains user-reported, not independently verified from wattage. No classification thresholds established and no IR/RF or relay commands sent. Next: repeat at manually selected speed 2 after stabilization.

### First supervised speed-2 samples (2026-10-20)

- User confirmed readiness after being asked to select speed 2 and allow 30 seconds of stabilization through the BroadLink app.
- Six actual Shelly readings, separated by five-second pauses: **25.30, 25.10, 25.10, 25.30, 25.30, 25.30 W**. Range **25.10–25.30 W**, mean **25.23 W**; relay ON throughout. Timestamped samples and user-reported state evidence saved to local SQLite.
- Mean is approximately **0.85 W** above the first speed-1 set, with a **0.60 W** gap between the observed ranges. This small separation in single short runs does not establish reliable speed classification; repeat measurements and physical speed confirmation will be needed before claiming that. No IR/RF or relay commands sent. Next: reported speed 3 after stabilization.


### First supervised speed-3 samples (2026-10-20)

- User confirmed readiness after being asked to select speed 3 and allow 30 seconds of stabilization through the BroadLink app.
- Six actual Shelly readings, separated by five-second pauses: **26.60, 26.70, 26.60, 26.70, 26.70, 26.70 W**. Range **26.60–26.70 W**, mean **26.67 W**; relay ON throughout. Timestamped samples and user-reported state evidence saved to local SQLite.
- Mean is approximately **1.43 W** above the first speed-2 set. Observed ranges remain separate in these short runs; speed classification is still unvalidated and labels remain user-reported. No IR/RF or relay commands sent. Next: reported speed 4 after stabilization, followed by a second standby pass.

### First supervised speed-4 samples (2026-10-20)

- User confirmed readiness after being asked to select speed 4 and allow 30 seconds of stabilization through the BroadLink app.
- Six actual Shelly readings, separated by five-second pauses: **30.00, 30.20, 30.10, 30.10, 30.20, 30.10 W**. Range **30.00–30.20 W**, mean **30.12 W**; relay ON throughout. Timestamped samples and user-reported state evidence saved to local SQLite.
- Initial user-reported state ranges: standby **0.00 W**, speed 1 **24.20–24.50 W**, speed 2 **25.10–25.30 W**, speed 3 **26.60–26.70 W**, speed 4 **30.00–30.20 W**. These short runs show separation, but reliable speed classification requires repeat measurements and independent physical speed confirmation. No thresholds enabled and no IR/RF or relay commands sent. Next: second standby pass with the Shelly relay left ON.

### Second supervised standby pass (2026-10-20)

- User confirmed the fan OFF after the speed-4 run. Six actual Shelly readings, separated by five-second pauses, were **all 0.00 W**, with relay **ON** throughout. Timestamped samples saved to local SQLite as standby calibration pass 2, with explicit user-reported state evidence.
- First measurement sequence complete: **36 successful samples** across standby, speeds 1–4, and standby again. Both standby passes match; sampled running power spans **24.20–30.20 W**. This supports a substantial steady-state standby/running separation under the tested conditions. Transition behavior and repeated speed ranges remain untested; no production classification thresholds have been enabled.
- No inconsistent readings requiring a room inspection were observed in this sequence. Physical speed labels remain user-reported from another room; wattage does not verify airflow direction. No IR/RF or relay commands were sent by Codex.
- Next milestone: supervised IR learning, followed by explicitly authorized single-command replay with fresh power observations. Before implementing speed inference, repeat measurements with physical speed confirmation, especially speeds 1 and 2 whose observed ranges were close.

### App self-capture experiment blocked (2026-10-20)

- User agreed to test capturing the RM4 Pro's own app-triggered speed-up signal. Initial pre-learning buffer read returned `StorageError`. A second attempt successfully called `enter_learning()`, but the first `check_data()` immediately returned `StorageError` again, before a usable supervised button-press window. Polling stopped; both failures were saved to SQLite with sanitized error types.
- No packet was captured, no active codebook was changed, and Codex sent no IR/RF commands. This result does **not** establish whether self-capture works: programmatic capture itself needs troubleshooting. The device's error name alone does not establish that app codes should be deleted or the device reset. Do not reset or remove the existing app configuration on that basis.

### Capture polling correction (2026-10-20)

- Investigated the earlier `StorageError` aborts. The upstream library discussion ([issue 404](https://github.com/mjg59/python-broadlink/issues/404)) and [Home Assistant learning implementation](https://github.com/home-assistant/core/blob/dev/homeassistant/components/broadlink/remote.py) handle `StorageError` alongside `ReadError` while waiting for a capture. The initial ad hoc scripts incorrectly treated that polling response as fatal; it was not evidence of exhausted storage.
- Retried with bounded polling that continues only for these two exceptions from `check_data()`; connection and learning-entry failures still stop the attempt. No library error checks were disabled or installed packages modified. User confirmed pressing speed up in the BroadLink app during the new capture window.

### Original-remote capture progress (2026-10-20)

- Corrected app self-capture test completed its 90-second window without a packet (81 `StorageError` polling responses); user reported pressing speed up in the app. This experiment did not demonstrate self-capture. Switched to the original remote at the user's request.
- Original remote produced four structurally checked IR captures. Assistant initially misunderstood the user's report of a possible wrong-button press as applying to the other commands. User clarified it applied **only to the physical power-button capture**. Restored confirmed speed-down and direction captures and retained a fresh, explicitly confirmed speed-up capture. Original power candidate is marked uncertain and excluded from the active codebook pending recapture; all raw candidates are preserved locally.
- Raw packets remain local and Git-ignored; none are replay-verified. No programmatic IR/RF replay or Shelly switching has occurred. Physical fan state must be re-established after the power recapture; speed and direction remain unverified.

### Four button captures saved; fan-display wake behavior reported (2026-10-20)

- Saved explicitly identified original-remote captures for **power_toggle, direction_toggle, speed_up, speed_down** in the configured local codebook, confirmed Git-ignored. Retained candidate files and the uncertain original power capture separately. Packet shape/length checks passed; no replay has been attempted and these are not physically verified programmatic commands.
- On the fresh power capture, user confirmed pressing POWER once but reported **no fan power-state change**. User explains that the **fan's display** needs waking: first arbitrary remote press wakes it, then a subsequent press operates the fan. BroadLink received an IR packet on the first press, so emission does occur; it is not yet established whether the same packet is consumed as wake-only by the fan or how long its display stays awake.
- Treat display wake state as another unknown before designing live control. **Never implement an unconditional double power toggle.** Validate wake/action behavior with supervised single transmissions, physical observations, and fresh Shelly readings. Do not infer failure from a wake-only response and blindly retry. Earlier successful wattage calibration labels remain user-reported; fan wake behavior was not instrumented during those manual changes.

### Display timing requirements to validate (2026-10-20)

- User reports the fan display returns to sleep **6 seconds after a wake or subsequent command**, and proposes **1 second between waking and sending the intended command**. These timings are user-reported, not yet measured by this project. User explicitly requested factoring the wake cycle into development, while deferring implementation until diagnostics/capturing are complete.
- Future tracking should distinguish believed awake/asleep from unknown, persist command/observation timestamps and uncertainty, and account for app/original-remote activity, restarts, and unconfirmed transmissions. A send timestamp alone cannot prove the fan received the signal or reset its timer.
- The suggestion to wake with an arbitrary command needs validation: an arbitrary command can perform its normal action if the display is already awake. Find a demonstrated safe wake strategy before automating any two-command sequence; never blindly double-toggle power or direction.
- User authorized proceeding with supervised replay diagnostics. Begin with a single power transmission bracketed by fresh read-only Shelly observations and a human report of display/fan response, with no automatic retry or wake sequence.

### First programmatic power replay, display unobserved (2026-10-20)

- Offline suite rerun before replay: **105 tests passed**. Inspected installed BroadLink transport and found automatic UDP retry behavior. The supervised diagnostic blocked a second UDP send using a scoped socket guard; production adapter was not modified. This retry behavior must be addressed before integrating live toggle control.
- With user authorization for supervised diagnostics, sent the captured POWER packet once: BroadLink acknowledged **one UDP transmission attempt**. Fresh Shelly reading before was **29.80 W**, then **30.00, 30.20, 30.20, 29.60 W** at roughly two-second intervals; relay ON throughout. Fan remained electrically consistent with running; no power-off response was observed. Command intent, acknowledgement, and before/after observations were saved to local SQLite `hardware_observations`; no real command was mislabeled as simulated in `command_attempts`.
- User clarified they could not reach the room in time to observe the display. Therefore wake behavior is **unobserved**, and unchanged watts cannot distinguish a wake-only response from a missed/ineffective command. No second packet was sent. Future display diagnostics must wait for explicit confirmation that the user is physically watching the fan before sending.
- Main controller remains dry-run; no unattended control or automatic wake sequence was enabled. Next: repeat one supervised command only after user is in position and confirms current fan/display state.

### Observed display wake from programmatic POWER (2026-10-20)

- User confirmed being in the room with this chat on their phone and authorized another single-command test. Sent one captured POWER packet with second UDP sends blocked; BroadLink acknowledged it. User directly observed **display woke; fan kept running**.
- Shelly measured **29.80 W before**, then **30.00, 30.10, 30.10, 29.80 W** afterward; relay ON throughout. Measurements, acknowledgement, and the user's physical observation are linked to the same local trial.
- This verifies that the captured packet can reach the fan and wake its display. It does **not yet verify power toggling while awake**, the proposed 1-second spacing, or the 6-second sleep/reset timing. No follow-up command was sent in this trial. Main controller remains dry-run.

### Successful supervised wake then power-off test (2026-10-20)

- User explicitly authorized **speed down → wait 1 second → power** while watching the fan. Each packet was transmitted once with additional UDP sends blocked. Power transmission began **1.002 seconds after the speed-down acknowledgement**; both requests were acknowledged.
- User directly observed **display woke, then fan stopped**. Shelly measured **29.70 W before**, then **3.00, 2.30, 0.00, 0.00, 0.00, 0.00 W** at roughly two-second intervals after power acknowledgement; relay ON throughout. Logged the plan, acknowledgements, actual interval, measurements, and human confirmation in local SQLite.
- This validates one supervised wake-and-power-off sequence with these captured codes and approximately 1-second spacing. It does not prove universal timing, the reported 6-second sleep timer, power-on behavior, or speed-down's intended action when already awake. In particular, **3.00/2.30 W occurred during shutdown** and must not be mistaken for stable standby or a confirmed speed.
- Current verified outcome: fan OFF with sustained 0.00 W and relay ON. Main controller remains dry-run; no automatic wake handling enabled. Remaining diagnostics include power-on replay, speed/direction actions while awake, repeated capture/replay reliability, and display sleep/reset timing.

### Supervised power-on replay: displayed speed 1 (2026-10-20)

- User authorized repeating speed down then power from standby. Fresh Shelly status was **0.00 W**, relay ON. Both requests acknowledged with one UDP send per command; power began **1.006 seconds after the speed-down acknowledgement**.
- After readings at roughly two-second intervals: **29.80, 26.70, 25.30, 25.30, 25.20, 25.00 W**, relay ON throughout. User directly confirmed **fan started at displayed speed 1**; confirmation linked to the trial in SQLite. These startup readings overlap the earlier user-reported speed-2 range, rather than the original speed-1 range. They are not a new stabilized calibration set; do not infer speed from wattage. No further commands sent.
- Do not yet designate speed down as a harmless wake command: the packet may also affect selected speed, or the fan may restore a different speed. Captured IR packets can contain repeated signal frames even when their outer repeat byte is zero; single UDP transmission does not alone establish a single effective button action. Investigate with direct display evidence before automating.

### Speed-up wake and action verified; initial wattage limits too narrow (2026-10-20)

- A single-speed-up diagnostic stopped before transmission at **23.40 W**, below the initial sample range. User confirmed fan still running normally at speed 1. Six additional readings were **23.40, 23.50, 23.50, 23.50, 23.60, 23.60 W** (mean **23.52 W**), relay ON. These show the first short calibration range was not a reliable hard boundary.
- Sent one speed-up packet: **23.60 W before**, then **24.00, 24.00, 23.90, 23.60 W**. User observed **display woke; stayed at speed 1**. One UDP send, no retry.
- A planned two-packet test also stopped before transmission at **23.20 W**. For the subsequent supervised speed-only diagnostic, relied on the user's directly confirmed running state plus a fresh positive power reading and relay ON, rather than using exact short-sample wattage limits. This is **not an unattended classifier or a production threshold change**.
- Sent two speed-up packets with **1.009 seconds from first acknowledgement to second send**. User observed **woke at speed 1, then changed to speed 2**. Shelly: **23.30 W before**, then **25.60, 25.20, 25.10, 24.70, 24.90, 24.80 W**; relay ON throughout. One UDP send per packet, no retries. Human observations linked to both successful trials in SQLite.
- Current directly observed speed is **2**. Captured speed-up now has a demonstrated wake-only response and one successful incremental action while awake. Initial wattage ranges vary/overlap across runs; do not use them to infer speed. Speed-down action, direction, and precise sleep/reset timing remain unverified.

### Wake-aware delivery foundation implemented (2026-10-20)

- User ended exhaustive physical diagnostics and authorized moving into development. No additional live device requests or IR transmissions were made during this implementation. Last physically confirmed state remains running at speed 2; it is historical evidence, not a fresh observation for future commands.
- Added `wake_delivery.py`: disabled-by-default delivery API, exclusive-control requirement, conservative display-awake estimate, 6-second reported timeout / 1-second wake delay with configurable margin, full quiet wait when uncertain, fresh reconciliation before action, and explicit invalidation for manual/app activity. Uses the short confirmed speed-up capture for wake. Does not reuse awake estimates after restart or infer physical success from acknowledgement.
- Added durable `delivery_attempts` / `delivery_events` tables and `delivery_store.py`. Intent precedes transmission; unresolved attempts survive crashes and block retries. macOS/Linux file locking excludes concurrent sends and resolution. Later matching power evidence can confirm a power action; speed/direction and interrupted deliveries require explicit manual verification/recovery. Additive migrations retain existing history and keep simulated-only command history separate.
- Updated the BroadLink adapter to use a one-send UDP transport for IR/RF delivery, with response validation and no acknowledgement-timeout retransmissions. No global socket monkeypatch is used in production. Existing disabled transmit gate remains in place. A captured packet may still contain multiple IR frames; this guard limits network sends, not embedded frames.
- Added documented environment controls, all comments above values; did not modify local `.env`, learned packets, or runtime history during development. Added `simulate_delivery.py` using fake hardware, fake time, and a disposable database. Demonstrates wake/action, skipping wake within the estimated window, and restart blocking while confirmation is pending.
- Validation: **130 offline tests passed**, offline delivery demonstration passed, `setup.sh` shell syntax passed, and diff whitespace check passed. Coverage includes lost acknowledgements, journal-write failure, restart/pending barriers, concurrent resolution, late/stale observations, external activity, timing expiry, and manual evidence. No live retest of the newly implemented transport/delivery layer performed.
- Work is on `codex/wake-aware-delivery`. `controller.py` remains dry-run; this milestone does not wire a live runtime, automatically classify watts, enable scheduling, or deploy. Remaining integration needs settled calibrated power evidence and explicit speed/direction verification rather than inferred states. User's instruction is to avoid exhaustive per-command physical tests.

### Guarded one-action controller integration (2026-10-20)

- Integrated `controller.py --live --exclusive-control` with the delivery layer. Both CLI opt-in and `HARDWARE_CONTROL_ENABLED=true` are required; default invocations and existing scheduled jobs remain dry-run. `--at` cannot send. No local enable setting or scheduling was changed.
- Added `power_observer.py`: logs raw read-only Shelly samples, requires consistent classifications across at least three samples and a configurable window, rejects invalid/stale timestamps, and treats relay OFF or threshold-gap/mixed readings as unknown. Standby/running thresholds must be explicitly configured; `.env.example` leaves them blank. No wattage-derived speed/direction inference.
- Added `hardware_runtime.py`: at most one policy action per live run, discards pre-action samples and manual-state attestations, waits for settling, then verifies power. Existing pending power attempts can be confirmed on later runs without issuing another action. Missing evidence or uncertain delivery blocks further sends. Current physical speed/direction can be supplied per invocation and expire; they are not permanent truth or scheduler settings.
- Added no-transmission recovery CLI for a specific attempt after manual inspection, and `history.py --deliveries N`. Recovery reads settled power and updates the journal; it never creates a blaster or retries. Speed/direction recovery requires the relevant physical observation.
- Linked attempts to controller run IDs and updated live history accounting. `command_sent=-1` represents pending/uncertain transmission, `1` acknowledged packets, and `0` no recorded attempt; physical confirmation remains separate. Live policy snapshots no longer claim dry-run behavior. Existing history is preserved with additive migrations.
- Validation: **150 offline tests passed**, including end-to-end controller → runtime → fake hardware, mixed/transition power windows, delayed confirmation, expired manual observations, opt-in gates, uncertain-send history, and recovery without transmission. Offline delivery demo, shell syntax and whitespace checks passed. No live device requests or transmissions occurred during integration; `.env` and raw learned packets were preserved.
- Current implementation supports explicitly enabled one-action control, not a fully unattended convergence loop. Before live operation, explicitly select standby/running thresholds from recorded calibration and enable hardware control; speed/direction actions still require physical verification before more actions. The user requested avoiding exhaustive additional hardware tests.

### Local enablement and supervised integrated evaluation (2026-10-20)

- User authorized configuring **standby ≤0.5 W / running ≥15 W**, enabling guarded live mode, performing a supervised controller evaluation, committing, and opening a PR. Updated only those three local `.env` keys; template remains disabled with blank thresholds. Normal invocations and existing scheduled jobs still require explicit `--live --exclusive-control` to transmit.
- During the first integrated run, user manually set speed 1 and turned the fan OFF after execution had begun. The controller had reserved a power-off intent but detected uncertain transition readings during the quiet wait and stopped **before any IR transmission attempt**. A follow-up run observed standby but stayed blocked on the unresolved intent. Inspection of the durable journal confirmed no wake/action send entries.
- Used explicit manual recovery with the user's shutdown report and fresh settled Shelly observations to resolve the unsent attempt; recovery transmitted nothing. Final real controller evaluation completed at **18:31 Indianapolis time** with **`satisfied — Fan already OFF`**. Policy requested off because it was outside the configured 22:00–07:30 schedule. No programmatic IR was emitted in these integrated evaluations, so they validate observation/gating/recovery but not the newly implemented transport on real hardware.
- Actual local climate settings remain **58°F stop / 62°F restart**, differing from the repository example/handoff defaults. Preserved these existing user settings. Current last confirmed physical state: fan OFF, outlet ON. No unattended live schedule was enabled.
- Rechecked **150 passing offline tests**, shell syntax, diff whitespace, and Git exclusion of `.env` and learned codes. GitHub main branch-protection lookup returned HTTP 404, so required-check enforcement remains unconfirmed; do not assume it blocks merges.

## Later exploration / backlog

- Validate Hallway room temperature against the Nest thermostat, or integrate Nest separately if needed.
- Assess Vornado shutter/window air leakage and the effectiveness/safety of potential sealing changes (avoid obstructing moving parts or airflow).
- Evaluate whether low-speed exhaust can draw warmer house air through the bedroom door gap; test experimentally, considering HVAC losses and combustion-appliance backdraft.
- Use historical temperature/command/power data to detect ineffective cooling, missed commands, unusual leakage, and possible direction mismatch; make automatic corrections only where justified by reliable evidence.
- Refine fan speed selection using measured effectiveness and noise, including indoor/outdoor differential.
- **Predictive target-time cooling (idea recorded 2026-10-19):** Learn actual time-to-cool from historical bedroom/outdoor temperature, indoor-outdoor difference, starting bedroom temperature, fan speed/direction and state, and, if available, humidity and HVAC/Flair conditions. Analyze measured cooling rate and uncertainty across prior cooling sessions, using verified running state to separate real fan operation from off/leakage periods. Eventually allow a configuration like **“reach 60°F by 22:30 Indianapolis time”** instead of only “start the fan at 22:00.” Estimate the required lead time and speed, dynamically adjust the planned start as observations arrive, and record predictions vs. observed outcomes to refine the model. Keep a conservative fallback to the existing fixed-schedule policy when insufficient data or stale/uncertain observations are available. This may require starting before the current 22:00 window, so explicitly define whether target-time scheduling may precondition outside the normal schedule before implementing it. **Future feature only; no predictive control currently implemented.**
- Coordinate Flair vent and Nest thermostat only when interference between systems is understood.
- Create historical charts and retention/backup workflows, and expand the framework to other home devices.

## Open questions / cautions

- Exact Vornado IR command behavior and whether any speed/direction state can be reliably established without manually observing it.
- Calibrated Shelly wattages and freshness thresholds; standby versus running can potentially be confirmed, **airflow direction cannot**.
- Ceiling fan RF code/frequency and BroadLink compatibility.
- Whether Flair provides a trustworthy sensor measurement timestamp; its room `state-updated-at` stayed unchanged when temperature values changed.
- Whether temperature-driven diagnostics can be made useful without frequent false alarms; they must remain advisory until proven.
- Long-term host, backup, and recovery procedures.

## Maintenance instructions for future contributors/assistants

- Read this log **and** the repository before planning the next task. Source code and tests take precedence if text is stale.
- After each milestone, move items between completed/pending, record calibrated facts and dates, and note any changed decisions.
- Distinguish **reported**, **simulated**, **physically validated**, and **deployed** claims.
- Keep secrets, private device identifiers, and raw learned remote codes out of this file.

### Runner migration and two-speed pivot (2026-10-21)

- New Mac runner installed with Python 3.13 and the five-minute dry-run LaunchAgent; real Flair/weather collection succeeded. Runtime data/codebook transferred by user.
- User requested only speeds 1 and 4 as desired states. Disabled speed-2/3 policy thresholds in local configuration and the example; retained the existing local speed-4 temperature threshold. Speeds 2/3 remain possible physical transition states.
- Endpoint wattage classification still requires fresh calibration; old short ranges are not production bounds. Direction cannot be inferred from watts. Unattended live control is not yet deployed. No offline tests run, per user instruction.

### Fresh endpoint calibration (2026-10-21)

- User physically confirmed settled intake speed 1, then intake speed 4. Recorded 12 read-only Shelly samples per state at three-second intervals on the new runner. Relay ON throughout.
- Speed 1: **24.90–25.10 W**, mean **25.00 W**. Speed 4: **30.10–30.30 W**, mean **30.24 W**. Raw timestamped samples are in ignored `data/endpoint_calibration/` and SQLite hardware observations.
- Endpoint sample ranges have a **5.00 W gap** in these runs. This supports the two-speed approach; it does not establish exhaustive long-term bounds or prove direction from wattage. Fresh standby and supervised endpoint transitions remain to verify. No IR or relay commands sent, no offline tests run, and scheduled control remains dry-run.

- Fresh standby calibration: 12 samples at three-second intervals, all **0.00 W**, relay ON throughout. User confirmed fan OFF after a 30-second settling period. Low endpoint classification alone cannot exclude physical speed 2 because historical speed-2 readings overlap speed-1 readings. Saturating endpoint behavior and restored direction must be physically verified before unattended endpoint control.


### Low/high endpoint control refactor (2026-10-21)

- Replaced optional speed-2/3/4 tiers with one optional `CLIMATE_HIGH_AT_F` threshold and existing temperature hysteresis. Low/high map to physical 1/4; desired and observed state validation rejects intermediate speeds. CLI flags use `low`/`high` and simulation previous states use `intake-low`/`intake-high`.
- Planner now requests logical `set_low` / `set_high` actions even when starting speed is unknown. Delivery expands each into four speed-down/up presses, separate from the existing wake, with configured spacing and awake-window checks before each press. Every press is durably journaled; partial/uncertain sequences remain blocked for inspection. Power/direction toggles retain their verification requirements.
- Preserved existing local calibration work and unrelated setup changes. Replaced local `.env` speed keys with `CLIMATE_HIGH_AT_F`, preserving the configured high threshold. Added no legacy aliases or data migrations; existing local latest desired state is already a supported endpoint/off state.
- No automatic wattage-based speed classification added: speed/direction still require physical verification after action. Endpoint saturation/idempotence is simulated, not newly physically validated. No live device calls, IR transmissions, scheduling, or enablement changes made.
- Validation: **152 offline tests passed**, including both endpoints from every physical starting speed, repeated endpoint requests, unknown starting speed, interruption/acknowledgement failure, display expiry/external activity during a sequence, and simulator button expansion. Offline delivery/fault/planning demonstrations, shell syntax, and diff whitespace checks passed.

### Follow-up refactor audit (2026-10-21)

- Audited production Python, CLI/configuration references, examples, and delivery behavior. No intermediate-speed targets or legacy speed-setting readers remain. Updated stale current-status/future-work guidance in this log; dated physical experiments retain their historical labels.
- Added tests for both endpoint sequences while already awake (exactly four action presses), and durable logging failure between presses (stops before the next transmission and blocks restart). **154 offline tests passed**; production AST/configuration scan, shell syntax, and diff whitespace checks passed.
- Confidence is in the software behavior under the modeled hardware contract. Real four-press saturation/timing and automatic wattage-based speed confirmation remain unvalidated/not enabled, respectively. No live hardware interactions performed.

### Persistent server path repair and commissioning (2026-10-21)

- User confirmed this Mac is the permanent host and requested scheduled operation. Repaired the moved virtual environment and reinstalled the existing LaunchAgent from the current project folder; the previous job referenced the former checkout. Preserved the configured 300-second interval and local climate settings. AC system sleep was already disabled.
- First run from the repaired LaunchAgent exited successfully, retrieved real Flair/weather readings, and saved SQLite history. The installed job remains dry-run. All 154 offline tests passed; dependency consistency and shell syntax checks passed. One sandboxed API check failed DNS resolution before the actual LaunchAgent succeeded; that failed check remains accurately recorded in history.
- User also requested progress toward unattended fan control and agreed to observe commissioning. Shelly read-only probe confirmed standby at 0.00 W with relay ON; BroadLink authentication succeeded. No unresolved delivery existed. Existing calibration files contain complete 12-sample low/high/standby trials matching the records above.
- User freshly confirmed fan OFF. One supervised guarded wake/power-on sequence completed with settled power confirmation (attempt `df0736b7-7184-4582-a046-d55ebd5d15b6`). Displayed speed and airflow direction still require the user's fresh observation. No scheduled live control has been enabled.

### Commissioned unattended intake deployment (2026-10-21)

- User confirmed startup restored **speed 4, intake**, then observed the integrated four-press low and high sequences reach **speed 1/intake** and **speed 4/intake** respectively. Both supervised endpoint journal barriers were manually resolved with these fresh observations and settled power evidence. User explicitly reserved the fan for automation and accepted intake as a commissioned setting, pausing before remote/app use.
- Added a separate `--unattended` mode and `unattended.py`. Local commissioning records the exclusive intake contract, initial physically verified endpoint, per-installation power consistency bands, and hardware/codebook/power-setting fingerprint. An endpoint model advances only after a complete acknowledged four-press sequence and agreement across all samples in the settled power window. The model never claims direct speed/direction sensing; physical verification columns remain empty for modeled state. The manual live path keeps its earlier requirements.
- Missing acknowledgements, partial sequences, pending endpoint delivery after a restart, and inconsistent post-command power leave the journal unresolved; no automatic retry. Disagreement with an established endpoint model latches a commissioning fault until inspection/recommissioning. A power action discards the previous speed estimate. A later scheduled invocation establishes the target endpoint.
- User confirmed the real unattended low-sequence trial reached **speed 1/intake**; its automatic power-consistency/journal resolution succeeded (attempt `9ff9734a-657f-4720-9523-b723c0e65dbb`). Settled low readings later drifted to **24.30 W**, so local consistency bands cover documented variation: **22.5–26.0 W low / 29.0–32.0 W high**. These are not speed classifiers: intermediate speeds can overlap low, and missed presses can escape the check. Direction is not detected, and unexpected power loss/manual use can invalidate the contract.
- Enabled the separate local unattended opt-in and installed `controller.py --unattended` every **300 seconds**. Preserved the actual **20:30–07:30 Indianapolis** window, **58°F stop / 62°F restart**, outdoor advantage, and high threshold. No credentials, packets, runtime data, or machine commissioning are committed.
- First live LaunchAgent invocation failed local Shelly access without sending IR. Python Local Network permission was subsequently shown enabled in System Settings. Retried the actual LaunchAgent: it successfully fetched climate inputs, requested OFF outside the window, delivered guarded power-off, and confirmed settled standby. Run 10 finished successfully at **20:18 Indianapolis time**, launchd last exit code **0**, no unresolved delivery. Historical failure logs remain intact.
- Validation: **167 offline tests passed**, covering commissioned-model transitions, process restart/pending behavior, missed commands, lost acknowledgements, whole-window checks, model/physical history separation, opt-in/configuration checks, CLI routing, and incompatible flags. Dependency consistency, shell syntax, and diff whitespace checks passed.

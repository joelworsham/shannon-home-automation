# Project log (working document)

> **Purpose:** Temporary, living record of completed work, open questions, and next steps. Use it for project continuity; do not treat it as a replacement for the code, `README.md`, or `.env.example`. Update after meaningful milestones, then archive or replace with GitHub Issues when appropriate.
>
> Last reviewed: **2026-10-20**. Status below reflects repository `main` plus locally reported tests. Statements about hardware are not confirmations until physically verified.

## Durable decisions

- **Architecture:** Python-first, locally hosted where practical; cloud services are acceptable when necessary. Google Home is an existing ecosystem, not a requirement for every device.
- **Source of truth:** Repository code and tests for behavior; `.env.example` for defaults; `README.md` for setup. Never commit `.env`, credentials, learned codebook, SQLite history, or logs.
- **Climate inputs:** Original Flair Puck for bedroom temperature/humidity, Open-Meteo for outdoor conditions. Flair Smart Vent retains its independent control until an intentional integration is made.
- **Fan outputs:** BroadLink RM4 Pro to transmit learned IR (Vornado Transom) and potentially RF (ceiling fan). RF protocol compatibility must be demonstrated.
- **Power observation:** Shelly Plug US Gen4 is for **read-only electrical monitoring**, never the routine means of power-cycling the Vornado. Its relay should remain on.
- **Safety:** Separate *desired*, *commanded*, and *observed* state. IR success cannot be inferred from sending a packet. Do not blindly re-toggle power/direction when state is unknown. Temperature anomalies are evidence, not proof of reversed airflow.
- **History:** SQLite stores measurements, decisions and reasons, config snapshots/changes, device evidence, diagnostic events, and failures. Preserve history across code updates.
- **Deployment:** Develop on the current Mac; intended local host is the studio Mac mini kept awake, with display sleep allowed. A macOS `launchd` installer exists but should not be enabled until explicitly requested.
- **Code delivery:** Favor direct GitHub changes (branch/PR when useful); otherwise complete canonical release snapshots, without competing `*_next` variants. Preserve machine-specific runtime files.

## Established operating preferences

Defaults in `.env.example` at this review (actual `.env` may override):

| Parameter | Default |
| --- | --- |
| Schedule (Indianapolis local time) | 22:00–07:30 |
| Cooling target / restart | 60°F / 64°F |
| Minimum outdoor cooling advantage | 5°F |
| Speeds 2 / 3 / 4 from bedroom temperature | Optional; example preset 62°F / 64°F / 68°F |
| Speed hysteresis | 0.5°F |
| Planned monitoring interval | Five minutes, 24/7 |

Prefer the quietest practical speed. Experimental exhaust-assisted warming should be treated as separate, opt-in functionality rather than assumed to work.

**2026-10-19 configuration change:** Repository defaults changed from 58/62°F to 60/64°F (target/restart). Speed thresholds remain 64/68/72°F. Existing machine-local `.env` values are not changed by repository updates; change them explicitly to adopt the new settings.

**2026-10-19 speed threshold change:** Set the four intended speed boundaries to **60/62/64/68°F for speeds 1–4**. The code expresses speed 1 as the default when active below the 62°F speed-2 cutoff; at or below the 60°F cooling target, the fan stops. Default speed 2/3/4 thresholds changed from 64/68/72°F to 62/64/68°F. Cooling restart remains 64°F; hysteresis remains 0.5°F. Machine-local `.env` values must be updated separately if they override defaults.

**2026-10-19 optional-speed pivot:** Speed 2/3/4 thresholds are independently optional. Blank or absent `.env` values disable their tiers; `setup.sh` does not append intentionally omitted speed settings. Speed 1 stays the implicit fallback when cooling is requested but no enabled higher threshold qualifies. With speed 2/3 unset and speed 4 at 60°F, speed 4 is requested for all active cooling until the 60°F stop target. Speed hysteresis must skip disabled tiers and handle previously desired speeds that have been disabled. Existing 60°F stop / 64°F restart / 5°F cooling advantage and dry-run-only safeguards remain unchanged. Configuration history can store disabled thresholds as `null`. This is a tested policy change, **not** validated physical fan control.

**2026-10-19 automated checks:** Added `.github/workflows/tests.yml` to run the complete offline `tests/` suite, dependency installation, and `setup.sh` shell syntax check on each pull request to `main` and each push to `main`. Manual `test_flair.py` / `test_weather.py` probes deliberately stay out of CI to avoid credential or live-device dependency. The expected required check is named **`tests`**. **Enforcement remains a separate GitHub settings task**: require the `tests` check in a `main` ruleset / branch protection, require PRs, and disallow bypass as appropriate. The current GitHub integration cannot edit that repository protection setting. The first pull-request workflow run ([Actions run 39274495353](https://github.com/joelworsham/shannon-home-automation/actions/runs/39274495353)) **passed**, including dependency installation, shell syntax, and offline tests. After merging, verify the first `main` push run as well.

## Work completed / validated to date

- **Flair API:** OAuth credentials tested locally, Bedroom and Hallway room objects returned, one original Puck found. Bedroom temperature/humidity compared successfully with Puck and Flair mobile app. Hallway temperature has **not** been separately validated as Nest's reading.
- **Weather:** Open-Meteo API tested against Indianapolis outdoor temperature/humidity/dew point.
- **Controller:** Configurable cooling/scheduling/four-speed policy and live dry-run execution verified locally. Sample daylight run correctly requested `off` due to schedule; **no real fan commands were sent**.
- **Persistence:** SQLite recorded actual sensor observations, effective configuration, run and decision history. Later dry-run also recorded a snapshot with **physical state unverified**. An empty diagnostics report under normal conditions is expected.
- **Safety/testing scaffolding:** Repository includes command planner, mock hardware, state-reconciliation and advisory temperature-diagnostic modules, hardware-fault simulation, and offline tests. Previous development reported **99 passing offline tests**; rerun against current commit when modifying code.
- **Device adapters:** Local BroadLink and read-only Shelly modules plus opt-in `hardware_probe.py` are in `main`; they have **not** been validated with actual devices.
- **Setup:** `setup.sh` handles dependencies/tests and offers optional `launchd` installation; repository is synced from development machine. Studio Mac has not been deployed.

## Next milestone: physical hardware validation (devices received)

**2026-10-20 hardware arrival:** User confirmed both the BroadLink RM4 Pro and Shelly Plug US Gen4 are in hand. **2026-10-20 setup update:** User reports **both devices provisioned and connected to Wi-Fi**, and knows the Shelly's local IP. BroadLink's IP still needs to be identified (router client list or local discovery). Local RPC authentication/reachability, learned IR/RF commands, and wattage calibration are **not yet verified**. Keep device IPs and other machine-specific details in the local `.env`, not this log. Begin with read-only local connectivity checks; the Shelly must remain powered and must not be used as the Vornado's routine switch.

Do these incrementally, on the home LAN, before enabling real automation:

1. Set up BroadLink RM4 Pro and Shelly Plug US Gen4. Record firmware/host identities locally and check network access using the existing probes.
2. Verify Shelly relay remains **on**, and calibrate watts for Vornado standby plus each of its four speeds. Record ranges/variance, and evaluate whether speed readings are distinguishable.
3. Learn and replay the Vornado remote signals. Test power/direction toggle behavior, IR line-of-sight, incremental speeds, reliability, and what happens after interruptions; keep automatic commands disabled initially.
4. Test the ceiling-fan remote's RF frequency/protocol and BroadLink learning/replay. Similar-looking 433 MHz remotes are only a clue; compatibility is unverified.
5. Calibrate/adjust safety policy and wire real measurements and commands into the controller **behind an explicit enable gate**, with conservative transitions, confirmation, logging, and failure behavior.
6. Execute supervised tests at different temperature/operating states, then deploy on the studio Mac mini and enable scheduled collection/operation only after verification.

### First physical connectivity probes (2026-10-20)

- **Shelly Plug US Gen4 — local API verified:** User ran `.venv/bin/python hardware_probe.py --shelly` and received `Shelly read-only: 0.00 W; outlet relay ON`. This confirms a successful read-only RPC response and that the plug relay was on at the time. **0 W is not yet a calibrated fan standby measurement**; verify actual Vornado operating and standby wattages later.
- **BroadLink RM4 Pro — LAN communication reached authentication, but auth failed:** User ran `.venv/bin/python hardware_probe.py --broadlink` and received `broadlink.exceptions.AuthenticationError: [Errno -1] Authentication failed` from `device.auth()`. IP discovery/targeting is adequate to reach an answering device; local authentication is **not yet verified**. Next supervised troubleshooting step: disable the BroadLink app's **Lock Device** setting, then rerun the existing no-transmission probe. If it still fails, inspect firmware/library compatibility and reassess pairing without changing hardware control code prematurely. Do not log local IPs, MAC addresses, or secrets here. No IR/RF transmissions were attempted.

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

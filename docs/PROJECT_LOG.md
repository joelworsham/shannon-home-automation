# Project log (working document)

> **Purpose:** Temporary, living record of completed work, open questions, and next steps. Use it for project continuity; do not treat it as a replacement for the code, `README.md`, or `.env.example`. Update after meaningful milestones, then archive or replace with GitHub Issues when appropriate.
>
> Last reviewed: **2026-10-18**. Status below reflects repository `main` plus locally reported tests. Statements about hardware are not confirmations until physically verified.

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
| Cooling target / restart | 58°F / 62°F |
| Minimum outdoor cooling advantage | 5°F |
| Speeds 2 / 3 / 4 from bedroom temperature | 64°F / 68°F / 72°F |
| Speed hysteresis | 0.5°F |
| Planned monitoring interval | Five minutes, 24/7 |

Prefer the quietest practical speed. Experimental exhaust-assisted warming should be treated as separate, opt-in functionality rather than assumed to work.

## Work completed / validated to date

- **Flair API:** OAuth credentials tested locally, Bedroom and Hallway room objects returned, one original Puck found. Bedroom temperature/humidity compared successfully with Puck and Flair mobile app. Hallway temperature has **not** been separately validated as Nest's reading.
- **Weather:** Open-Meteo API tested against Indianapolis outdoor temperature/humidity/dew point.
- **Controller:** Configurable cooling/scheduling/four-speed policy and live dry-run execution verified locally. Sample daylight run correctly requested `off` due to schedule; **no real fan commands were sent**.
- **Persistence:** SQLite recorded actual sensor observations, effective configuration, run and decision history. Later dry-run also recorded a snapshot with **physical state unverified**. An empty diagnostics report under normal conditions is expected.
- **Safety/testing scaffolding:** Repository includes command planner, mock hardware, state-reconciliation and advisory temperature-diagnostic modules, hardware-fault simulation, and offline tests. Previous development reported **99 passing offline tests**; rerun against current commit when modifying code.
- **Device adapters:** Local BroadLink and read-only Shelly modules plus opt-in `hardware_probe.py` are in `main`; they have **not** been validated with actual devices.
- **Setup:** `setup.sh` handles dependencies/tests and offers optional `launchd` installation; repository is synced from development machine. Studio Mac has not been deployed.

## Next milestone: physical hardware validation (pending devices)

Do these incrementally, on the home LAN, before enabling real automation:

1. Set up BroadLink RM4 Pro and Shelly Plug US Gen4. Record firmware/host identities locally and check network access using the existing probes.
2. Verify Shelly relay remains **on**, and calibrate watts for Vornado standby plus each of its four speeds. Record ranges/variance, and evaluate whether speed readings are distinguishable.
3. Learn and replay the Vornado remote signals. Test power/direction toggle behavior, IR line-of-sight, incremental speeds, reliability, and what happens after interruptions; keep automatic commands disabled initially.
4. Test the ceiling-fan remote's RF frequency/protocol and BroadLink learning/replay. Similar-looking 433 MHz remotes are only a clue; compatibility is unverified.
5. Calibrate/adjust safety policy and wire real measurements and commands into the controller **behind an explicit enable gate**, with conservative transitions, confirmation, logging, and failure behavior.
6. Execute supervised tests at different temperature/operating states, then deploy on the studio Mac mini and enable scheduled collection/operation only after verification.

## Later exploration / backlog

- Validate Hallway room temperature against the Nest thermostat, or integrate Nest separately if needed.
- Assess Vornado shutter/window air leakage and the effectiveness/safety of potential sealing changes (avoid obstructing moving parts or airflow).
- Evaluate whether low-speed exhaust can draw warmer house air through the bedroom door gap; test experimentally, considering HVAC losses and combustion-appliance backdraft.
- Use historical temperature/command/power data to detect ineffective cooling, missed commands, unusual leakage, and possible direction mismatch; make automatic corrections only where justified by reliable evidence.
- Refine fan speed selection using measured effectiveness and noise, including indoor/outdoor differential.
- **Predictive target-time cooling (idea recorded 2026-10-19):** Learn actual time-to-cool from historical bedroom/outdoor temperature, indoor-outdoor difference, starting bedroom temperature, fan speed/direction and state, and, if available, humidity and HVAC/Flair conditions. Analyze measured cooling rate and uncertainty across prior cooling sessions, using verified running state to separate real fan operation from off/leakage periods. Eventually allow a configuration like **“reach 58°F by 22:30 Indianapolis time”** instead of only “start the fan at 22:00.” Estimate the required lead time and speed, dynamically adjust the planned start as observations arrive, and record predictions vs. observed outcomes to refine the model. Keep a conservative fallback to the existing fixed-schedule policy when insufficient data or stale/uncertain observations are available. This may require starting before the current 22:00 window, so explicitly define whether target-time scheduling may precondition outside the normal schedule before implementing it. **Future feature only; no predictive control currently implemented.**
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

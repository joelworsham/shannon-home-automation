# Climate control feature tracker

Scoped 2026-10-21. Status describes repository implementation, not deployment or physical verification. This document tracks the extension requested after unattended intake commissioning. See [PROJECT_LOG.md](PROJECT_LOG.md) for hardware evidence and deployment history.

## Intended behavior

| ID | Feature | Status | Done when |
| --- | --- | --- | --- |
| SCH-1 | Sleep and all-other-times temperature presets | Scoped | Sleep window selects sleep settings; every other time selects the fallback preset instead of implicitly requesting OFF. |
| SCH-2 | Explain active settings | Scoped | CLI, history, and later app identify the active preset and effective target/restart/high threshold. |
| SCH-3 | Automatically calculate cooling start time | Not implemented; feature note added 2026-10-26 | Use historical cooling evidence, current bedroom/outdoor temperatures, and forecast outdoor temperatures to calculate when to start cooling for a configured target temperature and ready-by time. |
| CTL-1 | Timed temperature hold | Scoped | A request such as 68°F for one hour overrides presets until expiry, then uses the schedule active at that moment. |
| CTL-2 | Timed OFF hold | Scoped | OFF for four hours suppresses cooling across schedule changes, then returns to the current schedule. |
| CTL-3 | Indefinite automation pause | Scoped | Pause stops issuing fan commands until explicitly resumed; it does not itself turn the fan off. |
| CTL-4 | Safe manual operation and resume | Scoped | Pause acknowledges delivery has stopped; remote/button use invalidates modeled state; resume requires restored intake assurance and reconciles the endpoint. |
| API-1 | Private Mac command service | Scoped | Authenticated status/hold/pause/resume commands use the same durable state and guarded controller as local commands. |
| APP-1 | Personal Android remote | Scoped | Sideloadable app shows status and can set/cancel holds and pause/resume without embedding device or Flair credentials. |
| CFG-1 | Edit presets from phone | Deferred | Validate and persist settings on the Mac, with an audit trail; initial version continues using local config. |

## Architecture and implementation order

Keep the Mac as the sole automation authority. The phone submits intent; it never talks to BroadLink, Shelly, or Flair directly.

```text
Android app / local CLI -> command service -> durable control state
                                                   |
clock + presets + control state -> effective intent -> climate policy
                                                   |
                                      existing guarded hardware runtime
                                                   |
                                      SQLite decisions and delivery journal
```

### 1. Presets: a small policy extension

Begin with one named sleep window and one named fallback, rather than a general calendar engine. Keep the existing window and temperature keys as the sleep preset. Add an explicitly enabled fallback preset with its own target, restart, and optional high threshold. Keep outdoor advantage and speed hysteresis shared initially. Missing fallback configuration preserves today's outside-window OFF behavior; enabling it must be deliberate because this checkout runs live every five minutes.

Use half-open windows (start inclusive, end exclusive), the configured IANA timezone, and existing overnight behavior. Local wall-clock windows naturally repeat through a daylight-saving fold. Reject malformed configuration and invalid target/restart pairs before hardware work.

Schedule changes select new settings directly. Carry the previous desired fan state into the existing hysteresis logic: a running fan stops at the new target; an idle fan waits for the new restart threshold. Do not force an OFF cycle between presets. Log both the selected preset and effective thresholds, including outside-window fallback decisions; do not attribute fallback decisions to sleep temperatures in SQLite.

Acceptance: exact window boundaries, overnight/daytime windows, timezone/DST cases, both transition directions while running/off, optional high thresholds, invalid/partial fallback configuration, backward compatibility, simulation without writes, and effective-settings history.

### 2. Holds and pause: a shared control-state layer

Persist enabled/paused state separately from an optional active hold. Recommended first-version rules:

- Normal: evaluate the active preset.
- Temperature hold: replace the active preset's temperatures; keep outdoor cooling protection and hardware guards. This is a cooling target, not guaranteed room temperature or heating.
- OFF hold: request OFF regardless of preset. Unlike pause, this may send guarded power-off commands and needs no temperature API success to express its intent.
- Pause: issue no hardware commands, including OFF. Continue status/history collection where possible. Pause takes precedence over a hold and cancels it, so resuming cannot unexpectedly restore an old hold.
- New hold: replace the previous hold atomically. Reject while paused until automation is explicitly resumed.
- Cancel/expiry: return to the schedule active now, never a stored pre-hold preset. Store expiry as an absolute UTC timestamp; it survives process/Mac restarts and is checked on every evaluation.
- Resume: explicitly leave pause, then reconcile physical assumptions before allowing unattended cooling. Do not bypass fresh power reconciliation or physical direction inspection as a side effect.

For a one-number temperature hold, propose preserving the configured restart-minus-target gap (for example, target 68°F with a 4°F gap restarts at 72°F). Freeze those effective settings when accepting the hold. The high-speed threshold also needs an explicit rule; propose inheriting the active preset's absolute threshold initially. These are proposed UX choices, not implemented behavior.

Add an additive SQLite control-state migration plus immutable command events (request ID, source, accepted time, previous/new state, expiry). Invalid persisted control state must block live delivery rather than silently returning to schedules. Provide a local CLI first so the same operations can be exercised without Android or networking.

**Concurrency matters:** delivery currently has its own file lock, but policy selection and external commands do not share one control lock. Add a consistent lock order and recheck effective intent before delivery. Pause must wait for an already-started guarded sequence to finish or block, then acknowledge quiescence; do not interrupt halfway through an endpoint sequence. An acknowledged pause must prevent stale scheduled work from subsequently sending. Do not report receipt as completed shutdown. Keep API acceptance, evaluation, packet acknowledgement, and physical power confirmation distinct.

**Physical remote use:** unattended control now overrides remote power/speed changes on the next evaluation when fresh settled power disagrees with the target. Matching low/high power bands skip transmission; overlapping intermediate-speed wattages may escape detection. It automatically reconciles incomplete power/endpoint deliveries using fresh settled power. Pause is still needed when manual settings should persist. Watts cannot detect a changed airflow direction; after a direction change, physically restore/inspect intake before resume. Ordinary power/speed use no longer requires recommissioning. A phone button cannot establish unseen physical direction.

Acceptance: fake-clock expiry and restart persistence, schedule changes during holds, replacement/cancel behavior, paused commands, stale/malformed state, simultaneous controller/CLI requests, pause during delivery, and resume after manual changes without bypassing journal barriers.

### 3. Private command service on the Mac

Keep a small authenticated API over the same application functions as the CLI. Suggested contract (not implemented endpoints):

| Operation | Proposed request | Result |
| --- | --- | --- |
| Status | `GET /status` | Active preset, control state, hold expiry, last evaluation/readings with timestamps, power observation, modeled endpoint, faults, pending command. |
| Temperature hold | `POST /hold` with target, duration, request ID | Durable acceptance and command ID; controller outcome follows separately. |
| OFF hold | `POST /hold` with mode OFF, duration, request ID | Same acceptance/outcome distinction. |
| Cancel hold | `DELETE /hold` | Resume current preset if enabled. |
| Pause | `POST /pause` | Paused/quiescent acknowledgement after delivery coordination. |
| Resume | `POST /resume` | Resume or explicit inspection-required result. |

Validate finite temperatures, positive bounded durations, request size, and supported modes. Duplicate request IDs return the original outcome rather than extending holds or repeating actions. Use a separate revocable application token; keep it out of URLs/logs/Git and store it in phone-private storage. No credentials or raw learned codes in responses.

Start with loopback binding and an explicitly configured private transport. A LAN-only setup is sufficient at home; away-from-home access remains a choice. Recommended private remote option: Tailscale on Mac and phone, with the local API exposed through tailnet-only HTTPS using [Tailscale Serve](https://tailscale.com/docs/features/tailscale-serve). No public router forwarding is needed for that design. Transport setup is separate from application authentication.

The current five-minute LaunchAgent is not an interactive service. API acceptance alone would otherwise leave a command waiting nearly five minutes. Introduce one serialized worker with command wakeups and periodic evaluations, or an equivalently coordinated wake mechanism; retain single-action-per-evaluation hardware behavior. Show queued/converging status because power-on and endpoint establishment can require separate evaluations. Install the API/worker with a supervised launchd lifecycle only after offline validation; avoid two competing schedulers.

Acceptance: authentication, duplicate requests, disconnected clients, restart recovery, concurrent commands, API failure while automation continues, unavailable sensors, and no accidental public binding. Define queued-command behavior explicitly; initial app should never queue offline commands for later surprise execution.

### 4. One-screen Android app

Use a small native Kotlin app. No store publication, accounts, cross-platform layer, phone background scheduler, or push notifications in v1. The Mac remains authoritative even when the app is closed.

Screen: automation status, active preset, recent bedroom/outdoor temperatures with freshness, hold countdown, target + duration input, **Hold temperature**, **Hold OFF**, **Cancel hold**, **Pause automation**, and **Resume automation**. Separate OFF from pause in the labels. Display an inspection-required state when safe resume is blocked. A compact connection setup stores server address and token; v1 has no preset editor.

Build a debug APK and install on the user's phone through Android Studio or `adb`; [Android's device deployment guide](https://developer.android.com/studio/run/device) documents USB and wireless debugging. Preserve package ID and signing key for updates. Record phone Android version before choosing the minimum SDK. Show connection failures and stale data plainly; never imply accepted commands were physically completed. Do not resend a stale hold automatically after reconnecting.

Acceptance: actual phone install, pairing, each command round-trip, expiry shown from server state, stale/offline status, rejected resume, update installation, and status after closing/reopening the app. An APK is not considered delivered until built; phone behavior is not considered verified until tested on-device.

## Future feature: predictive cooling start time (SCH-3)

Calculate the start time automatically from a desired temperature and ready-by time, rather than relying only on a fixed evening start. Extend the predictive target-time cooling idea recorded in [PROJECT_LOG.md](PROJECT_LOG.md).

- Learn cooling duration/rate from historical bedroom and outdoor temperatures, starting room temperature, fan endpoint/power evidence, and recorded experiment/HVAC conditions where available. Account for missing data and distinguish commanded state from observed running evidence.
- Look up forecast outdoor temperatures ahead of time, covering the possible cooling window through the ready-by time. Use the expected temperature trajectory and indoor/outdoor advantage, alongside current readings and historical performance, to estimate the required lead time.
- Refresh the forecast and recalculate as actual temperatures arrive. Report the proposed start time, expected target-arrival time, confidence, and whether the target appears achievable. Record forecast issue/retrieval times, predictions, actual start/arrival times, and prediction errors so performance can improve over successive nights.
- Define an allowed earliest start and whether preconditioning may begin outside the existing schedule. Preserve outdoor-advantage checks, temperature hysteresis, holds/pause when implemented, and existing hardware guards. Use the fixed schedule as a fallback when history or forecasts are insufficient, stale, or unavailable.

Acceptance when implemented: replay historical nights using only information available at each prediction time; handle warmer/colder forecasts, forecast changes or outages, sparse history, unattainable targets, timezone/DST boundaries, and earliest-start limits; compare predicted versus actual arrival times before enabling automatic scheduling.

Status: feature note only. Target ready-by time, earliest permitted start, and model/confidence rules remain to be chosen; current scheduling is unchanged.

## Scope boundaries and decisions still needed

- First two presets: sleep and fallback. User's existing sleep values remain untouched; daytime values need selection before live enablement.
- Hold target hysteresis and high-speed inheritance above are proposals to confirm before shipping holds.
- Pause versus OFF semantics above are the recommended distinct controls. Indefinite OFF can use an OFF hold without expiry later, if wanted.
- Home Wi-Fi only versus away-from-home access determines transport setup; app/backend structure supports either.
- Keep intake-only behavior. General schedule calendars, heating, exhaust, remote preset editing, charts, and predictive cooling (SCH-3 above) remain out of this first extension.
- Recommended sequence: presets -> durable controls/local CLI -> private API/worker -> Android. Holds and concurrency/resume behavior are the largest backend change; the app should be a thin client once those are settled.

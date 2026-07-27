# HA Integration — Entity Design

Draft for review. No code yet.

## Repos

| Repo | Purpose | PyPI |
|---|---|---|
| `marvin-connected-home` | Async API client — auth, REST, SignalR. Platform-agnostic, reusable outside HA. | `marvin-connected-home`, import `marvin_connected_home` |
| `ha-marvin-connected-home` | HA custom integration (HACS). Depends on the above. | n/a |

HA integration domain: `marvin_connected_home`.

Rationale for dropping the `lib-` prefix: it's a C/JS convention, not Python. Repo is named after the package. Note `marvin` on PyPI is taken (PrefectHQ), so the fuller name is needed anyway.

---

## Scope: this is a public HACS integration

It must work for people whose hardware, wiring and account differ from the reference setup. Two consequences that shape everything below.

### Nothing about the reference installation may be hardcoded

The reference account has three casement/awning windows, one house, four dry-contact wires, and percentages of 20/60/100. None of that can be assumed. In particular the dry-contact mapping is **read from the API to pre-fill the config flow, then confirmed by the user** — never baked in. See the fallback section.

### Entity creation is capability-gated, never assumed

Marvin Connected Home spans more hardware than the reference setup covers. The API advertises capabilities per device, and we create entities strictly from those flags:

| Flag | Gates |
|---|---|
| `sashInstalled` / `hasSash` | `cover` (sash) |
| `shadeInstalled` / `hasShade` | `cover` (shade) |
| `ledInstalled` / `hasLed` | `light` |
| `hasPrivacy` / `hasTinting` / `hasAutoTint` | privacy-glass entities |
| `hasEBrakeInstalled` | obstruction `binary_sensor` |
| `closedSenEnabled` | closed-sensor `binary_sensor` |
| `manLockSenEnabled` | manual-lock `binary_sensor` |

Asset kinds seen in the API: `windowStates`, `skylightStates`, `clicStates`, `groupStates`, with `assetType` on each asset.

**Verification status — must be stated plainly in the README:**

| Hardware | Status |
|---|---|
| Modern Automated Casement / Awning | **Verified** against real hardware |
| Awaken skylight (shade + LED + IAQ) | Inferred from API and app constants |
| Modern Automated Multi-Slide Door | Inferred |
| CLiC privacy glass / tinting | Inferred |
| Server-side groups | Inferred |

Everything inferred ships behind capability flags and must degrade to "no entity" rather than a crash. Unknown `assetType` values get logged at debug and skipped, so new Marvin hardware can't break an existing install.

### Defensive parsing is a hard requirement

Never assume a field exists. Never assume a type. Sentinels (below) are not the only surprise the API has produced — `outdoorHumidity` came back as `-2147483648.0` (a float holding an int sentinel) while `indoorHumidity` was a double sentinel, so even the *type* of "no data" is inconsistent between sibling fields.

### Other multi-tenant concerns

- **Multiple houses.** Accounts can hold several (`Select House` exists in the Control4 driver). Config flow must let the user choose, and support more than one.
- **Roles.** `role`, `roles`, `userrole`, `adminApp` appear in the API — a guest or non-admin user may be unable to write config or command devices. Surface permission failures as a clear error, not a silent no-op.
- **Units.** Temperature may be °C or °F per account/region. Must be determined, not assumed — see open questions.

---

## Device model

One HA **device** per Marvin asset (`Asset_<uuid>`), i.e. one per window.

```
DeviceInfo(
  identifiers={(DOMAIN, asset_id)},
  name=asset["name"],                          # "Primary Awning"
  manufacturer="Marvin",
  model=state["boardType"],                    # "CAAWNE"
  sw_version=state["onUnitFirmwareVersion"],   # "03.02.00"
  connections={(CONNECTION_NETWORK_MAC, state["networkInfo"]["mac"])},
)
```

The MAC connection makes HA link the device to the UniFi network entity automatically — a nice free win.

A second device represents the **house** (`House_<uuid>`) for house-scoped entities.

**Availability:** all window entities follow `status.deviceOnline`. When the cloud itself is unreachable, entities go unavailable *unless* dry-contact fallback is configured (see below).

---

## Window entities

### cover (primary)

| | |
|---|---|
| Platform | `cover`, `device_class: window` |
| Features | `OPEN`, `CLOSE`, `SET_POSITION`, `STOP` |
| `current_cover_position` | `state.sashOpen` (0–100) |
| `is_closed` | `not state.isSashOpen` |
| `is_opening` / `is_closing` | derived: `targetSashPercent` vs `sashOpen` |

Commands map to `POST /commands` with `{"Type":"sash","Command":"sash","Value":<pct>,"Schema":"integer"}`.

**Stop** has no native API command. The app implements it by re-issuing `sash` with the current `sashOpen`. We do the same. (Note: the *hardware* dry contact terminal 2 is a true stop — so the fallback path can stop more cleanly than the cloud path.)

Position streams live during travel via SignalR, so `is_opening`/`is_closing` and intermediate positions are genuine, not simulated.

### binary_sensor

| Entity | Source | device_class | Notes |
|---|---|---|---|
| Lock | `state.windowLocked` | `lock` | HA semantics: `on` = unlocked, so invert |
| Rain detected | `state.sensorDetectedRain` | `moisture` | Per-device, actually works |
| Obstruction | `state.eBrakeStatus` | `problem` | Only created if `hasEBrakeInstalled` |
| On battery / supercap | `state.onBattery` | `battery` | Mains lost, running on supercaps |
| Fault | `errors.errorCount > 0` | `problem` | `errorList` as attribute |
| Closed sensor | `state.closedSensor` | `opening` | Diagnostic; only if `closedSenEnabled` |

### sensor (diagnostic)

| Entity | Source | Unit / class |
|---|---|---|
| Wi-Fi signal | `state.wiFiRSSI` | dBm, `signal_strength`, diagnostic |
| Target position | `state.targetSashPercent` | %, diagnostic |
| Last heartbeat | `status.lastHeartbeat` | `timestamp`, diagnostic |
| Last command | `status.lastCommandReceived` | `timestamp`, diagnostic |
| Control path | internal | `cloud` / `dry_contact` / `unavailable` — see fallback |

### Firmware reporting

A window exposes **four independent firmware versions**, and they genuinely differ — on the reference unit: `wcB` 04.09.00, `onUnit` 03.02.00, `rainSensor` 03.02.00, `mcB` 03.01.00. Reporting a single number would be actively misleading when diagnosing behaviour that depends on a specific board.

All four are surfaced, three ways:

| Where | What |
|---|---|
| `DeviceInfo.sw_version` | `wcBfirmwareVersion` — the window control board, the version users track |
| Diagnostic sensors | One per component: `wcB`, `onUnit`, `rainSensor`, `mcB`, `remote` |
| Diagnostics download | All of them, always |

The per-component sensors are `EntityCategory.DIAGNOSTIC` with `entity_registry_enabled_default = False` — present for anyone who wants them, hidden by default so five extra entities per window don't clutter a multi-window install. This is the idiomatic HA answer to "expose it but don't spam"; stuffing them into attributes on another entity would hide them from history and templating.

`remoteFirmwareVersion` is empty on the reference unit; its sensor is only created when non-empty.

### switch / number (config)

| Entity | Source | Notes |
|---|---|---|
| Close when raining | `configSettings.closeWhenRain` | switch |
| Buzzer | `configSettings.buzzerDisabled` | switch, inverted |
| On-unit LEDs | `configSettings.oucLEDEnabled` | switch |
| Contact position 2/3/4 | `configSettings.hA2/3/4Position` | `number`, 0–100, config category |

Exposing the `hA*Position` values as `number` entities is a genuine bonus — it lets the dry-contact stops be retuned from HA.

**Writes are now verified.** `POST /setconfig/{assetId}` with `{"key":…,"value":"<string>"}`. All six keys above confirmed round-tripping. Note every value is a *string* even for ints and bools — the client serialises accordingly.

These ship writable in v1, `hA*Position` included.

### button (actions)

| Entity | Endpoint | Status |
|---|---|---|
| Check for firmware update | `POST /devices/performota/{deviceId}` `{}` | **Verified** |
| Reboot | `POST /devices/reboot/{deviceId}` `{}` | **Unverified placeholder — do not ship** |
| Recalibrate | `POST /devices/recalibrate/{deviceId}` `{}` | **Unverified placeholder — do not ship** |

These take the **internal device id** (`eval3-…`), not the asset id.

Reboot and recalibrate stay behind a flag until captured. Recalibrate in particular drives the sash through a full travel cycle, so it should carry a confirmation in the UI even once verified.

---

## House-wide cover

`POST /commands` accepts a **House id** in `DeviceId`, broadcasting to every asset in the house — this is what the app's "airflow" button does (`sash 50`, then `sash 0`).

Worth exposing as a house-level `cover` entity ("All Windows"), because one API call beats N and it matches what the app offers. It also answers the group-command question: server-side groups are reachable the same way.

**Caveat for the README:** this is genuinely house-wide, not per-room. A user pressing it expecting one window will move all of them.

## Rename

`PUT /houses/{houseId}/assets/{assetId}` with `{"assetName":…,"assetType":…}` is verified.

HA convention is that renaming an entity is local and does not propagate upstream, so this is **not** wired to the HA rename. Better as an optional service (`marvin_connected_home.rename_asset`) for users who want their Marvin app and HA names kept in sync.

---

## House entities

| Entity | Source | Notes |
|---|---|---|
| Auto venting | `autoVentingEnabled` | switch |
| Away mode | `awayModeIsActive` | switch |
| Open condition met | `openConditionMet` | binary_sensor, diagnostic |
| Indoor temp / humidity / dew point / VOC / AQI | house state | sensors |
| Outdoor temp / humidity / dew point / AQI | house state | sensors |
| Outdoor conditions | `outdoorConditionsDesc` | sensor |

### Sentinel handling — mandatory

Unpopulated numerics return **type minimums, not null**:

- `-1.7976931348623157E+308` (double min) — temperature, humidity, dew point
- `-2147483648` (int min) — VOC, air quality, `isRaining`
- `"Unknown"` — `outdoorConditionsDesc`

Every one of these **must** map to `None` → `unavailable`. Without this, HA records −1.8e308 °C into the recorder and long-term statistics are permanently poisoned.

On the reference account all environment fields are currently sentinels, with `autoVentingEnabled: false`. Working hypothesis: the Air Algorithm only populates them when auto-venting is enabled (Marvin's docs describe hourly weather polling against the house zip code). **Unverified** — test by enabling auto-venting for a day. Create the entities regardless; they self-populate if data appears.

---

## Dry-contact fallback

Entirely optional, and off unless configured. Most installs will have no dry contacts wired at all, some will have only a close wire, some all five. The design must cover all of those without complaint.

### Hardware background

Terminals on the window controller (from Marvin's wiring instructions):

| Terminal | Function |
|---|---|
| 1 | Switch common |
| 2 | Stop sash at current position |
| 3 | Position 3 |
| 4 | Position 2 |
| 5 | Position 1 |
| 6 | Close and lock |

Contacts are **momentary and edge-triggered** — any switch rated ≥3 V / 25 mA, no minimum duration. The wiring doc's factory defaults are expressed in degrees (10° / 20° / 45°), but firmware 4.x moved to **percentages**, which is also what the API speaks (`sashOpen`, `targetSashPercent`, `hA*Position` all 0–100). We use percent throughout and ignore the degree figures.

### Config

Per asset, all optional:

| Option | Type | Purpose |
|---|---|---|
| `close_switch` | `switch` entity | terminal 6 — close + lock |
| `position_switches` | list of `{entity, position_pct}` | terminals 5/4/3, any subset |
| `stop_switch` | `switch` entity | terminal 2 — often unwired (4-wire runs are common) |
| `contact_sensor` | `binary_sensor` entity | real sash open/closed, if the user fitted one |
| `pulse_duration` | seconds, default 0.5 | ignored if the switch self-pulses |
| `notify_on_switchover` | bool, default `true` | persistent notification toggle |

### Position mapping — suggested, never hardcoded

The config flow reads `configSettings.hA2Position` / `hA3Position` / `hA4Position` from the device and offers them as **pre-filled suggestions** next to each switch selector, with the user free to override.

Working assumption for the suggested ordering:

| Config key | Terminal | Doc label |
|---|---|---|
| `hA2Position` | 5 | Position 1 |
| `hA3Position` | 4 | Position 2 |
| `hA4Position` | 3 | Position 3 |

This ordering is **unverified** — it's the natural ascending interpretation and matches the reference unit's 20/60/100, but the correspondence isn't stated anywhere in the API or the docs. Because the value is only ever a *suggestion the user confirms against their own wiring*, a wrong guess costs a config-flow correction rather than a mis-driven window. That's precisely why it isn't hardcoded.

Anyone can verify their own mapping in minutes: fire one relay channel, read `sashOpen` back. Worth putting in the README as a setup step.

### Behaviour

Failover is **automatic**, triggered when the cloud API is unreachable **or** `deviceOnline` is false.

- Requested position snaps to the nearest configured contact. Going to 55% with contacts at 20/60/100 fires the 60% contact, and the cover reports 60 — not 55.
- Without a `stop_switch`, `STOP` is unsupported while degraded and raises rather than silently no-oping.
- The `Control path` sensor always reflects reality (`cloud` / `dry_contact` / `unavailable`), so automations can branch on it.
- Persistent notification on switchover, honouring `notify_on_switchover`.

### State while degraded — the honest part

With no cloud, there is no position feedback. Behaviour:

- **With `contact_sensor` configured:** `is_closed` comes from the real sensor. Position is reported as 0 when closed, otherwise the last commanded contact position, with an attribute flagging it as inferred.
- **Without one:** `is_closed` is `None` (unknown) and position is `None`. The cover stays *controllable* but reports unknown state.

Deliberate choice: never fabricate a position. A cover that claims 60% while actually closed is worse than one admitting it doesn't know.

---

## Open questions

Ordered by how much they block the build.

1. **Reboot and recalibrate are uncaptured.** Both exist in the app; neither was triggered. API.md carries a placeholder guessed by analogy with `performota` (`POST /devices/reboot/{deviceId}`, `{}`), explicitly marked unverified. The `button` entities for these must not ship until captured — a wrong path merely 404s, but a wrong body against a live endpoint might not be so benign. **Action item: capture when convenient and safe.**

2. **Token lifetime is unobserved.** Access token TTL is 1 hour and the capture was too short to see a refresh. The iPhone app staying logged in for months strongly suggests long-lived sliding refresh tokens (B2C's default is a 14-day sliding window, which never expires in practice if the client refreshes regularly) — but that's the *app's* session, and it doesn't guarantee our client gets refresh tokens at all. That depends on whether `offline_access` is in the granted scopes. **First thing the client proves out**, because if it needs interactive re-auth hourly the integration isn't viable as designed.

3. **Temperature units.** Could be °C or °F per account or region. `/defaults` is the likely source. Must not be assumed — guessing wrong silently corrupts recorder history, same class of bug as the sentinels.

4. **Non-sash commands unverified** — shade, LED, lock, CLiC, tinting come from app action constants only, with no hardware to test against. All capability-gated; ship as best-effort and mark clearly as untested in the README.

5. **Group commands unverified.** `groupStates` and `HouseGroupStateUpdated` exist, and the Control4 driver had group proxies, so server-side groups are real. Command shape unknown.

6. **`Bearer Bearer`** — the app sends a doubled scheme. Test whether a single `Bearer` is accepted; only replicate the bug if the API rejects the correct form.

7. **Environment sensors return sentinels on the reference account** with `autoVentingEnabled: false`. Hypothesis is that the Air Algorithm populates them only when auto-venting is on. Unverified, and it may simply be that casement/awning units lack the IAQ hardware that Awaken skylights carry.

---

## Non-goals

- **Local control.** Not possible without firmware modification; see RESEARCH.md. The devices listen on no TCP ports.
- **Replacing the dry contacts.** They remain the only internet-independent path and the integration treats them as a fallback, not a migration target.
- **Device provisioning / onboarding.** BLE commissioning stays in the Marvin app.

## Release hygiene

- **Diagnostics download must redact** bearer tokens, SignalR access tokens, email, MAC, IP, and house/asset UUIDs.
- **Reauth flow** required, given the auth model.
- **Options flow** for the dry-contact mapping and notification toggle, so they're editable after setup.
- **README must be explicit** about verified vs inferred hardware. Users with skylights or multi-slide doors should know they're the first to test that path.
- Unofficial-API disclaimer: Marvin has re-platformed once already (Google Cloud IoT Core → Azure), so the API can change without notice.

# HA Integration — Entity Design

Design notes for the integration. Reflects what ships.

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
- **Units.** Determined, not assumed: temperatures are **Fahrenheit**. There is no unit field in the API and Marvin sells into the US and Canada only. Established on one account — see open questions.

---

## Device model

One HA **device** per Marvin asset (`Asset_<uuid>`), i.e. one per window.

```
DeviceInfo(
  identifiers={(DOMAIN, asset_id)},
  name=asset["name"],                          # "Primary Awning"
  manufacturer="Marvin",
  model=state["boardType"],                    # "CAAWNE"
  sw_version=state["wcBfirmwareVersion"],      # "04.09.00" -- the board owners track
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
| Control path | internal | `cloud` / `dry_contact` / `unavailable` — see fallback |
| Dry contacts | internal | summary of the fallback wiring, plus where to change it |

### Firmware reporting

A window exposes **four independent firmware versions**, and they genuinely differ — on the reference unit: `wcB` 04.09.00, `onUnit` 03.02.00, `rainSensor` 03.02.00, `mcB` 03.01.00. Reporting a single number would be actively misleading when diagnosing behaviour that depends on a specific board.

All four are surfaced, three ways:

| Where | What |
|---|---|
| `DeviceInfo.sw_version` | `wcBfirmwareVersion` — the window control board, the version users track |
| Diagnostic sensors | One per component: `wcB`, `onUnit`, `rainSensor`, `mcB`, `remote` |
| Diagnostics download | All of them, always |

The per-component sensors are `EntityCategory.DIAGNOSTIC`, which keeps them off dashboards and collapsed on the device page while still recording history. Stuffing them into attributes on another entity would hide them from history and templating, which defeats the point for firmware-dependent debugging.

`remoteFirmwareVersion` is empty on the reference unit; its sensor is only created when non-empty.

### switch / number (config)

| Entity | Source | Notes |
|---|---|---|
| Close when raining | `configSettings.closeWhenRain` | switch |
| Buzzer | `configSettings.buzzerDisabled` | switch, inverted |
| On-unit LEDs | `configSettings.oucLEDEnabled` | switch |
| Contact position 1/2/3 | `configSettings.hA2/3/4Position` | `number`, 0–100, config category |

Exposing the `hA*Position` values as `number` entities is a genuine bonus — it lets the dry-contact stops be retuned from HA.

**Writes are now verified.** `POST /setconfig/{assetId}` with `{"key":…,"value":"<string>"}`. All six keys above confirmed round-tripping. Note every value is a *string* even for ints and bools — the client serialises accordingly.

These ship writable in v1, `hA*Position` included.

### button (actions)

| Entity | Endpoint | Status |
|---|---|---|
| Check for firmware update | `POST /devices/performota/{deviceId}` `{}` | **Verified** |
| Reboot | `POST /devices/gen2/reset/reboot/{deviceId}` `{}` | **Verified** |
| Recalibrate | `POST /devices/gen2/activate/calibrate/{deviceId}` `{}` | **Verified** |

These take the **internal device id** (`eval3-…`), not the asset id. All three are fire-and-forget: a plain-text acknowledgement, no request id, nothing to poll. A successful press means the cloud accepted the command, not that the device carried it out — the outcome arrives later over SignalR.

Recalibrate drives the sash through a full travel cycle, which makes an accidental press expensive: a bedroom window opens fully and closes again, possibly while nobody is home. Home Assistant has no per-entity confirmation, so it ships with `entity_registry_enabled_default=False` — an owner has to enable it deliberately, and the README asks for a `confirmation:` block on any dashboard card exposing it. That is weaker than a real dialog, and it is the strongest guard the entity model offers.

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
| Open on / Close on temperature | `temperatureOpenIfEnabled`, `temperatureCloseIfEnabled` | switch, config |
| Open on / Close on humidity or dew point | `humidityDewPointOpenIfEnabled`, `humidityDewPointCloseIfEnabled` | switch, config |
| Auto venting temperature low / high | `temperatureLowerLimit`, `temperatureUpperLimit` | `number` °F, config |
| Auto venting humidity low / high | `humidityLowerLimit`, `humidityUpperLimit` | `number` %, config |
| Auto venting moisture metric | `humidityDewPointToggle` | `select` — humidity or dew point |
| Open condition met | `openConditionMet` | binary_sensor, diagnostic |
| Indoor temp / humidity / dew point / CO₂ / VOC / PM2.5 / air quality | `environment` | sensors |
| Outdoor temp / humidity / dew point / air quality | `environment` | sensors |
| Outdoor conditions | `outdoorConditionsDesc` | sensor |

**Away mode is deliberately not an entity.** `awayModeIsActive` exists, but only in
`GET /houses` under `state` — and that endpoint nulls `preferences`, while
`GET /houses/{id}` (which the coordinator uses) nulls `state`. Surfacing it would
mean a second request on every poll for one boolean that the app itself offers no
way to change. It also sits among `halioMode`, `autoTintingIsActive` and
`hasAutoTint`, which suggests it belongs to CLiC privacy glass rather than to
windows. `House.away_mode` is parsed and available to anyone who wants it; no
entity is created.

Temperatures are **Fahrenheit** — see API.md's units section.

**Write keys are not read keys.** These entities all store the *read* spelling
(`temperatureUpperLimit`); the client translates to the write spelling
(`tempUpperLimit`) on the way out. Storing the write spelling would round-trip
through the client fine and then match nothing on read, leaving the entity
permanently unknown. Only the four temperature keys differ.

**Dew-point limits are not writable.** `humidityDewPointToggle` switches the
algorithm between relative humidity and dew point, and both values are verified
writes. But the app never wrote `dewPointUpperLimit` / `dewPointLowerLimit`
during capture, so their write spellings are unknown — and after the
`temperature` → `temp` surprise, guessing is not defensible. Selecting dew point
works; setting its thresholds needs the Marvin app. Documented on the `select`
entity itself.

Unset limits arrive as the int sentinel (`-2147483648`), so the `number`
entities map that to unknown rather than rendering minus two billion.

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

Failover is **automatic**, triggered when the cloud API is unreachable **or** `deviceOnline` is false. The library carries short request timeouts (15 s) precisely so this decision happens in seconds — before them, a hung connection meant a cover command blocked for aiohttp's five-minute default before falling back.

- Requested position snaps to the nearest configured contact — but **only among contacts that move in the requested direction**. Going to 55% with contacts at 20/60/100 fires the 60% contact, and the cover reports 60 — not 55. An *open* request never fires the close relay, even when 0 is the nearest configured position: with only a close contact wired, "open" would otherwise close the window, the exact inversion of the user's intent. Such a request raises instead, and `OPEN`/`SET_POSITION` are not advertised while degraded unless a stop above 0 exists.
- A *close* request errs the other way: without a close contact it snaps to the lowest stop, since moving toward closed is the right failure mode for a window it may be raining on.
- Without a `stop_switch`, `STOP` is unsupported while degraded and raises rather than silently no-oping.
- The `Control path` sensor always reflects reality (`cloud` / `dry_contact` / `unavailable`), so automations can branch on it. Its `degraded_reason` attribute distinguishes `reauthentication_required` / `cloud_unreachable` / `device_offline` — same degradation, different remedies.
- Persistent notification on switchover, honouring `notify_on_switchover`. When the degradation is a dead session rather than an outage, the notification says so and points at re-auth instead of blaming the network.
- **A dead session degrades like an outage but is reported as what it is.** The coordinator tracks whether the last poll failed on authentication (`auth_failed`); the relays stay usable either way, because a window you can still close during rain beats doctrinal purity. Only a *per-command* auth error or rejection refuses to fall back — the contacts cannot fix those and would mask them.

### State while degraded — the honest part

With no cloud, there is no position feedback. Behaviour:

- **With `contact_sensor` configured:** `is_closed` comes from the real sensor. Position is reported as 0 when closed, otherwise the last commanded contact position, with an attribute flagging it as inferred.
- **Without one:** `is_closed` is `None` (unknown) and position is `None`. The cover stays *controllable* but reports unknown state.

Deliberate choice: never fabricate a position. A cover that claims 60% while actually closed is worse than one admitting it doesn't know.

---

## Implementation status

Everything in this document ships and runs against real hardware, except where
noted below.

| Area | Status |
|---|---|
| Auth (B2C, PKCE, manual-paste redirect, reauth) | **Verified** |
| Token refresh, with **every** rotation persisted back to the entry | Implemented — see below |
| `cover` — 0–100% positioning, live travel, stop | **Verified** |
| `binary_sensor`, `sensor`, `switch`, `number`, `button` | **Verified** |
| SignalR real-time push | **Verified** |
| Diagnostics download with redaction | Implemented |
| Dry-contact fallback | Implemented; selection logic, command orchestration (failover trigger, pulse release/edge-guard, notification latch, pulse serialisation) unit-tested, **failover never executed against real relays** |
| Reboot / recalibrate buttons | **Verified** — recalibrate disabled by default |
| Auto-venting preference writes | **Verified** — note the `temp*` / `temperature*` read-write key asymmetry |
| `PreferencesUpdated` push | **Verified** — house preferences update live rather than on the 5-minute poll |
| Fahrenheit temperatures | **Verified** on one US account; see open questions |

### Token rotation persistence

An earlier revision of this table claimed rotation persistence was verified
when the code only persisted the rotation performed **at setup**. Every later
renewal (roughly hourly) rotated the token in memory only, so after the first
hour of uptime the entry held a stale credential — and since the old token must
be assumed single-use, any restart from that point risked a forced re-login.

The provider now takes an `on_refresh_token_update` callback and the
integration persists **every** rotation into the config entry the moment it
happens. The library's token-endpoint handling also distinguishes 4xx (bad
credential → `ConfigEntryAuthFailed`, reauth flow) from 5xx (B2C outage →
`ConfigEntryNotReady`, silent retry), so a transient Microsoft-side blip no
longer shows the user a reauth prompt.

### Partial-push defence

`AssetUpdated` has carried the full asset in every capture, but
`GET /houses/{id}` proves the API sends *stub* assets in some contexts, so the
coordinator no longer replaces cached assets wholesale. `merge_assets()` (in
the library) merges the push over the cache field-preservingly: pushed values
win, sections the push omits keep their cached values, devices match by id. A
partial push therefore cannot flip config switches and contact positions to
unknown for the five minutes until the next poll. To settle whether partial
pushes actually occur, log raw frames (`on_raw_message`) across a config write
and a firmware update and check whether `configSettings`/`status` are always
present — until then the merge makes the answer not matter.

### Entity defaults

Every entity is enabled by default except `cover.<house>_all_windows`, which is
disabled because one press moves every window in the house.

An earlier revision disabled all diagnostic and config entities. That was wrong:
`entity_category` already keeps them off dashboards and collapses them on the
device page, so hiding them bought almost nothing — while costing recorder
history, which cannot be backfilled once an entity is enabled. It was worst for
`wifi_rssi`, on hardware whose Wi-Fi link is known to be marginal. The rule now
is: if an entity is worth creating, it is worth recording; if it is not worth
recording, do not create it.

Note that changing `entity_registry_enabled_default` only affects **new**
installs. Home Assistant writes `disabled_by: integration` into the entity
registry at first registration and never revisits it, so existing installs need
each entity enabled by hand.

### Discoverability

Options flows are easy to lose track of, so each window carries a **Dry
contacts** diagnostic sensor whose state is the reachable positions (or
`Not configured`) and whose attributes spell out the full mapping plus a
`configure_at` pointer. Home Assistant only renders an entity's *state* in the
device list — attributes are behind the entity's More Info dialog — so the state
string is chosen to be useful on its own, including a `(no stop)` suffix when
terminal 2 is unwired.

---

## Open questions

1. **Temperature units are Fahrenheit — on the evidence of one account.**
   There is no unit key anywhere in the API (`/defaults` returns `{"data": []}`),
   the app's limits read as Fahrenheit, and forcing the Android device to Celsius
   did not change the app's display. Marvin sells into the US and Canada only, so
   the integration now declares Fahrenheit. If a metric account ever surfaces,
   this becomes per-account rather than a constant — and changing it again would
   reinterpret existing recorder history, so it is worth getting a second account
   confirmed before assuming it is settled.

2. **Non-sash commands unverified** — shade, LED, lock, CLiC and tinting come
   from app action constants only, with no hardware to test against. All
   capability-gated, so they simply produce no entity on hardware that lacks
   them.

3. **Environment sensors return sentinels on the reference account**, with
   `autoVentingEnabled: false`. The hypothesis is that the Air Algorithm
   populates them only when auto-venting is on; it may instead be that
   casement/awning units lack the IAQ hardware Awaken skylights carry. The
   entities exist and self-populate if data ever appears.

4. **The `hA*Position` → terminal mapping is still undocumented.** The
   integration does not depend on it — the fallback uses positions the user
   declares — but the config flow's pre-filled suggestions assume
   `hA2`→terminal 5, `hA3`→4, `hA4`→3. Anyone wiring this up should confirm
   against their own hardware by firing one relay channel and reading the
   position back.

5. **The failover path has never run against real relays.** Its selection logic
   has unit tests, but no relay has been pulsed by this code. Worth exercising
   deliberately — block access to `azapi.marvin.com` for a minute — rather than
   discovering its behaviour during a real outage.

---

## Non-goals

- **Local control.** Not possible without firmware modification; see RESEARCH.md. The devices listen on no TCP ports.
- **Replacing the dry contacts.** They remain the only internet-independent path and the integration treats them as a fallback, not a migration target.
- **Device provisioning / onboarding.** BLE commissioning stays in the Marvin app.
- **Marvin's server-side groups.** Group CRUD is captured and works, but groups
  have **no command endpoint**: commanding one in the app emits a single
  `POST /commands` with one entry per member, fanned out client-side. A group is
  an organisational label, and Home Assistant already has areas, labels and
  groups that do the job better. Mirroring Marvin's would add a sync problem
  and buy nothing.
- **Notification preferences.** `GET`/`PUT /notificationpreferences/{houseId}`
  is captured and works — twelve toggles covering rain, obstruction, power loss,
  product-offline and auto-open/close alerts. They control **push notifications
  to the Marvin phone app**, and nothing else: Home Assistant never receives
  those pushes, so mirroring the toggles would let you configure a channel HA
  cannot observe. Home Assistant notifies off the rain, obstruction and fault
  entities directly, with far better routing than Marvin's fixed categories.
  Nothing is lost by leaving these in the app.
- **Event history.** `POST /houses/{houseId}/events` returns Marvin's own feed —
  `{title, message, eventTime, assetId, imageURL}`. Across a 54-event sample the
  titles reduce to five: *Window locked*, *Window unlocked*, *Window opened*,
  *Window closed*, *Window sensed rain*. Every one is already an entity here,
  already updated sub-second over SignalR, and already recorded in the logbook
  with more precision than a sentence of prose. The feed carries no event that
  cannot be derived from existing state. (It does cover periods before the
  integration was installed, but Home Assistant cannot backfill historical
  states anyway, so that is not a usable advantage.)
- **Schedules and automations.** The app's Hermes bundle carries `CREATE_SCHEDULE`,
  `BUILD_SCHEDULE`, `DELETE_SCHEDULE` and `AUTO_SELECT_RAIN`, but a full sweep of
  the app's UI produced **no schedule or automation endpoint at all** — what the
  app labels "Automations" is the auto-venting preferences plus notification
  toggles, both of which are covered. Those constants appear to be unused for
  this hardware. Home Assistant's own automation engine is the better home for
  this regardless.

## Release hygiene

- **Diagnostics download must redact** bearer tokens, SignalR access tokens, email, MAC, IP, and house/asset UUIDs.
- **Reauth flow** required, given the auth model. It verifies the new sign-in can actually see the entry's house and aborts with `reauth_account_mismatch` otherwise — signing in with the wrong Marvin account would otherwise "succeed" and then break at the next poll.
- **The config flow's OAuth `state` is random per flow** and checked on paste-back. A pasted URL carrying someone else's state is rejected (`state_mismatch`); a bare code is accepted, since it carries no state to check. PKCE binds the code; state binds the URL.
- **Options flow** for the dry-contact mapping and notification toggle, so they're editable after setup.
- **README must be explicit** about verified vs inferred hardware. Users with skylights or multi-slide doors should know they're the first to test that path.
- Unofficial-API disclaimer: Marvin has re-platformed once already (Google Cloud IoT Core → Azure), so the API can change without notice.
- **CI** runs on both repos: ruff + strict mypy + pytest on the library; pytest, hassfest and HACS validation here. The library release process is: tag `vX.Y.Z` on the library, which triggers its `release.yml` workflow to build and publish that version to PyPI; then pin `manifest.json`'s requirement to `marvin-connected-home==X.Y.Z` and bump the integration version.

## Roadmap

- ~~**Publish the library to PyPI and pin by version**~~ — done 2026-07-30.
  The manifest previously installed the library from a **git tag**, and tags are
  mutable: anyone who compromised the GitHub account could silently change what
  every install pulled. `marvin-connected-home` is now on PyPI, published from
  the library repo by GitHub Actions using [trusted publishing][tp] (OIDC, no
  API token), and the requirement is a plain `==` version pin.

[tp]: https://docs.pypi.org/trusted-publishers/

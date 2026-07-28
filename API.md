# Marvin Connected Home — Unofficial API Reference

Reverse-engineered from the Marvin Home Android app (`com.marvin.home`, versionCode 81102). Unofficial and undocumented; Marvin may change it without notice.

Base URL: `https://azapi.marvin.com/mch-prd/1.0/`

## Authentication

Azure AD B2C.

| | |
|---|---|
| Tenant | `marvinwindowsb2c.onmicrosoft.com` |
| Tenant id | `96f9eb15-6ccd-4351-9ca0-94cd182c0bdb` |
| Issuer | `https://marvinwindowsb2c.b2clogin.com/{tid}/v2.0/` |
| Public client id | `0d117826-a605-4d81-999e-ae67e85de895` |
| Policy | `B2C_1A_AuroraSignInRegister` |
| Other policies | `B2C_1A_AuroraPasswordReset`, `B2C_1A_AuroraProfileEdit` |
| Redirect URI | `aurora://login/verify` |
| Scopes | `openid offline_access` |
| Flow | authorization code + PKCE |
| Token TTL | 3600 s |
| Refresh | verified working; refresh tokens rotate on use |

**Header quirk:** the app sends `authorization: Bearer Bearer <jwt>` — a doubled scheme, evidently a bug on Marvin's side. **The correct single `Bearer` also works** (verified), so there is no need to replicate it.

**The redirect is a custom URI scheme**, so no web server can receive it, and new redirect URIs cannot be registered against Marvin's tenant. Headless and desktop clients must collect the authorization code by hand — Chrome keeps the failed `aurora://` URL in the address bar; Safari discards it, in which case read the `Location` header of the final 302 with DevTools *Preserve log* enabled.

**The bearer is the `id_token`, not an access token.** With `scope=openid offline_access` and no resource scope, B2C returns only `id_token` and `refresh_token`; the app sends the id_token, which is why a working bearer carries `aud=<client_id>` and an `emailAddress` claim.

## Identifier types — mind which one each endpoint wants

Three different id forms are in play, and they are **not** interchangeable:

| Form | Example | Used by |
|---|---|---|
| Asset id | `Asset_<uuid>` | `setconfig`, `requestconfig`, `requeststatus`, rename, `commands` |
| House id | `House_<uuid>` | `houses/*`, `preferences`, `commands` (house-wide) |
| Internal device id | `eval3-<serial>` | `devices/performota` |

An asset contains one or more devices; the device carries the state and firmware.

## Response formats are inconsistent

Three different conventions are in play.

**Write endpoints return plain text**, and not even consistently cased:

| Endpoint | Success body |
|---|---|
| `setconfig` | `Ok` |
| asset rename | `OK` |
| `preferences` | `Success` |
| `performota` | `Perform OTA sent to device: '<deviceId>'` |
| `commands` | JSON |

**Read endpoints return double-encoded JSON.** The body is a JSON *string* whose content is the real document, so it starts with a quote rather than a brace and must be decoded twice:

```
'"{\r\n  \"data\": [\r\n    {\r\n      \"id\": \"House_...\"'
```

The same double encoding appears in SignalR's `arguments[0]`, so it looks deliberate rather than accidental. A client that only checks for a leading `{` will silently treat every read as plain text.

**Read payloads are wrapped** in `{"data": [...], "meta": ..., "errors": ...}`.

## Key casing is not stable between endpoints

`GET /assets/{assetId}` returns `WCBfirmwareVersion` and `MCBfirmwareVersion`; SignalR and `/houses/{id}/assets` return `wcBfirmwareVersion` and `mcBfirmwareVersion`. Look these up case-insensitively.

---

## Endpoints

### Read

| Method | Path | Notes |
|---|---|---|
| GET | `/users` | account, roles |
| GET | `/houses` | house list |
| GET | `/houses/{houseId}` | house-level only — `groupStates`, `preferences`, `environment`, plus **asset stubs** (`{id, name}`) |
| GET | `/houses/{houseId}/assets` | **full asset tree**, including nested `devices` and their state |
| GET | `/assets/{assetId}` | flat device state for one asset (different key casing — see above) |
| GET | `/defaults` | config defaults. Verified to contain **no** unit keys, so temperature units come from elsewhere |
| GET | `/requestconfig/{assetId}` | current `configSettings` |
| GET | `/requeststatus/{assetId}` | async command/config result |
| GET | `/requeststatus/{houseId}` | house-scoped equivalent |

`GET /houses/{houseId}/assets/{assetId}` does **not** exist (404) — that path is PUT-only, for rename.

### House payload layout

`GET /houses/{houseId}` → `data[0]`:

- `id`, `name`, `type`, `zipCode`, `latitude`, `longitude`
- `groupStates` — house-wide aggregate (`hasSash`, `sashOpen`, `windowLocked`, …) with nested `windowStates` / `skylightStates` / `clicStates`. These are **objects, not asset collections**.
- `preferences` — auto-venting config: `autoVentingEnabled`, per-metric `*UpperLimit` / `*LowerLimit`, `*OpenIfEnabled` / `*CloseIfEnabled`, `rainCloseIfEnabled`, `autoVentingOverrideMinutes`, `smartHomeIntegrationEnabled`
- `environment` — a **nested object**, not inlined at house root: `indoorTemperature`, `indoorHumidity`, `indoorDewPoint`, `indoorCO2`, `indoorVOC`, `indoorPM25`, `indoorAirQuality`, the `outdoor*` counterparts, `isRaining`, `outdoorConditionsDesc`
- `assets` — stubs only

### Commands

```http
POST /commands
{"commands":[{"DeviceId":"Asset_<uuid>","Type":"sash",
              "Command":"sash","Value":50,"Schema":"integer"}]}
```

Batch array. Response echoes per-command `code` / `message`.

**`DeviceId` accepts a House id**, which broadcasts to every asset in the house — this is how the app's "airflow" button works (`sash 50` then `sash 0` against `House_…`). It is also the group-command mechanism.

`Value` is a percentage, 0–100. `0` closes and locks.

**No native stop.** The app implements stop by re-issuing `sash` with the current `sashOpen`. (Hardware dry-contact terminal 2 is a true stop; the API has no equivalent.)

Command types seen in the app bundle but **unverified**: `shade`, `lights`, `led_color`, `led_intensity`, `lock`, `clic`, `tinting`, `auto_tint`.

### Config write

```http
POST /setconfig/{assetId}
{"key":"hA3Position","value":"55"}
→ 200  Ok
```

**All values are strings**, whatever the underlying type: `"55"`, `"true"`, `"false"`. One key per request.

Verified keys:

| Key | Type | Meaning |
|---|---|---|
| `hA2Position` | int-as-string, 0–100 | dry-contact position 1 (terminal 5) |
| `hA3Position` | int-as-string, 0–100 | dry-contact position 2 (terminal 4) |
| `hA4Position` | int-as-string, 0–100 | dry-contact position 3 (terminal 3) |
| `closeWhenRain` | bool-as-string | rain auto-close |
| `buzzerDisabled` | bool-as-string | on-window sound (inverted) |
| `oucLEDEnabled` | bool-as-string | on-window switch LED |

Terminal correspondence for `hA*Position` is an assumption, not documented. See DESIGN.md.

### Rename asset

```http
PUT /houses/{houseId}/assets/{assetId}
{"assetName":"Primary Awning","assetType":"window"}
→ 200  OK
```

`assetType` must be included.

### House preferences

```http
POST /houses/{houseId}/preferences
{"autoVentingEnabled":true}
→ 200  Success
```

Here booleans are **real JSON booleans**, unlike `setconfig`. Other preference keys (temperature/humidity/dew-point/AQ limits, `awayModeIsActive`) presumably use the same endpoint — unverified.

### Firmware update

```http
POST /devices/performota/{internalDeviceId}
{}
→ 200  Perform OTA sent to device: 'eval3-…'
```

Takes the **internal device id**, not the asset id. Empty JSON body.

### Reboot / Recalibrate — NOT CAPTURED

The app offers both. Neither was triggered, so both are unknown.

**Placeholder assumption**, by analogy with `performota`:

```
POST /devices/reboot/{internalDeviceId}        {}
POST /devices/recalibrate/{internalDeviceId}   {}
```

**This is a guess and must not ship unverified** — a wrong path is harmless (404), but a wrong *body* against a real endpoint might not be. Action item: capture these.

---

## Real-time state (SignalR)

```http
POST /v1.1/messages/negotiate?negotiateVersion=1
→ {"url":"https://signr-mch-prd-<cluster>.service.signalr.net/client/?hub=home",
   "accessToken":"<separate 1h JWT>"}
```

Azure SignalR Service, hub `home`. Then negotiate again against that URL and open a WebSocket.

Server-to-client methods:

| Target | Payload |
|---|---|
| `AssetUpdated` | full asset JSON (same shape as within `/houses/{houseId}`) |
| `HouseGroupStateUpdated` | group state |

`arguments[0]` is a **JSON string**, not an object — parse twice.

**Latency verified as sub-second, including out-of-band changes.** A dry-contact close (entirely outside the cloud) produced live progressive `sashOpen` updates and the final `windowLocked` confirmation. Position streams during travel, so intermediate values are real.

## Sentinel values

Unpopulated numerics return **type minimums, not null**, and inconsistently between sibling fields:

| Value | Meaning | Seen on |
|---|---|---|
| `-1.7976931348623157E+308` | double min | `indoorTemperature`, `indoorHumidity`, `indoorDewPoint`, `outdoorTemperature`, `outdoorDewPoint` |
| `-2147483648` | int min | `indoorVOC`, `indoorAirQuality`, `outdoorAirQuality`, `isRaining` |
| `-2147483648.0` | int min **as a float** | `outdoorHumidity` |
| `"Unknown"` | string | `outdoorConditionsDesc` |

Every one must be mapped to null. Note `outdoorHumidity` uses the int sentinel in a float field while `indoorHumidity` uses the double sentinel — even the type of "no data" is inconsistent, so check both magnitudes on every numeric.

Per-device `sensorDetectedRain` is a genuine boolean and does populate, unlike the house-level `isRaining` aggregate.

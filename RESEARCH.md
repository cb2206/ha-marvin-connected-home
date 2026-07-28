# Marvin Connected Home → Home Assistant: Research Findings

Date: 2026-07-26

## TL;DR

This document is the research log, written as the investigation ran. Some
intermediate conclusions were overturned by later evidence; where that happened
the original reasoning is kept, because how a wrong conclusion was reached is
usually more useful than the conclusion. The findings below are the settled ones.

1. **No Home Assistant integration existed.** Only Crestron Home and Control4
   had drivers, both cloud-based. This work produced the first HA integration.
2. **There is no local control path.** The windows are ESP32 devices holding a
   single outbound MQTT/TLS connection to **Azure IoT Hub**, with *zero*
   listening TCP ports. Verified by a full 65,535-port sweep against real
   hardware, with same-VLAN peers as a control.
3. **Marvin's sanctioned partner API polls, with real-world state lagging up to
   10 minutes** — stated in their own Control4 driver documentation.
4. **But the app-facing API is far better, and that is what the integration
   uses.** It pushes over Azure SignalR in **sub-second** time, including for
   changes that never went through the cloud: a dry-contact relay close produced
   live progressive position updates and a final lock confirmation. The
   10-minute figure is a restriction on the partner API, not the platform.
5. **The whole surface was reverse-engineered from the Android app** and is
   documented in API.md. Auth is Azure AD B2C with a mobile-only redirect, which
   is why the integration's config flow asks the user to paste a URL back.

---

## Verified architecture

Established from your live network, not from marketing material.

### Your three units

| IP | MAC | Signal | AP / channel | Notes |
|---|---|---|---|---|
| 10.0.2.x | `48:27:e2:xx:xx:xx` | -57 dBm | <ap-name>, ch 1 | 36.5% TX retry rate, CCQ 30 |
| 10.0.2.x | `48:27:e2:xx:xx:xx` | -64 dBm | `<mac>`, ch 6 | |
| 10.0.2.x | `48:27:e2:xx:xx:xx` | -66 dBm | `<mac>`, ch 6 | |

- Hostname on all three: `Marvin-Connected-Home`
- VLAN 2, network "IoT internet access", SSID `<ssid>`, 2.4 GHz (`ng`), 20 MHz
- All three **online**, uptime ~3.6 days. (The UniFi client *list* view reports them offline — that field is stale; the raw client object and live flow records show them up.)

### Hardware

MAC OUI `48:27:E2` = **Espressif Inc.** (registered April 2022). Combined with the user guide's LED table listing a "Successful Bluetooth Pair" indication, these are almost certainly **ESP32** (Wi-Fi + BLE in one part).

### Cloud transport — confirmed

UniFi traffic flows captured the outbound connection:

```
10.0.2.x → <azure ip>  TCP
             iot-mch-prd-<cluster>.azure-devices.net
```

That is **Azure IoT Hub**. `mch` = Marvin Connected Home, `prd` = production, `c1-01` = cluster instance.

The user guide corroborates: it requires **outbound TCP 8883** (MQTT over TLS) and warns that if the port is blocked, "the window will be unable to establish a connection with the Marvin cloud and app controls and configurations will not be possible."

Because the MQTT session is long-lived, it does not appear in the flow log during normal operation — only NTP (`pool.ntp.org`, every ~60 min) and DHCP renewals show up. The MQTT flow record only surfaces on reconnect.

### Platform history

The app originally ran on **Google Cloud IoT Core**, which Google shut down in August 2023 (an App Store review from 08/2023 complains that "the Google IoT shutdown messed things up"). Marvin migrated to Azure IoT Hub. Worth knowing: this platform has been re-hosted once already, so any cloud integration you build carries that risk again.

### Local attack surface — none

Full 65,535-port TCP sweep of `10.0.2.x`: **nothing open.** (Port 53 appeared open, but that is your UniFi gateway's DNS intercept responding, not the device.) Targeted sweep of `10.0.2.x` and `10.0.2.x` across 18 common IoT/embedded ports: **nothing open.**

No HTTP, no local MQTT broker, no CoAP, no ESPHome-style native API, no `esp_local_ctrl`. The devices are pure outbound MQTT clients.

**Control test — ruling out the Tailscale path.** The scan ran from a Mac reaching the IoT VLAN over a Tailscale subnet route, so an empty result could in principle have been the tunnel or a UniFi inter-VLAN rule rather than the devices. Scanning known-listening peers on the *same* VLAN over the *same* path settles it:

| Host | Open ports |
|---|---|
| `ratgdo32-<id>` (ESPHome) | 80, 3232, 6053 |
| `span-gateway` | 22, 80, 443, 8080, **8883** |
| `HS103` (Kasa) | 9999 |
| `TeslaWallConnector` | 80 |
| **All three Marvin units** | **none** |

Port 8883 in particular traverses fine to `span-gateway`. The Marvin result is real.

### Control paths that exist today

| Path | Local? | Notes |
|---|---|---|
| On-unit buttons | Yes | Works with no network |
| Dry contacts / wall switch | Yes | Your current method; works with no network |
| Marvin Home app | No | Cloud → Azure IoT Hub → device |
| Alexa | No | Cloud-to-cloud |
| Control4 (Chowmain driver) | No | Vendor states "bi-directional **cloud-based** communications/feedback" |
| Crestron Home | No | Same |

Note the guide also states windows close and lock automatically on power loss, and that rain/obstruction sensing is on-device — those safety behaviours do not depend on the cloud.

---

## Q1: Has anyone built an HA integration?

**No.** Searched HA core integrations, HACS/custom-component repos, GitHub, the HA community forum, and Reddit. Nothing. ("Marvin" hits on GitHub are all *Amazing Marvin*, the to-do app, or unrelated projects.)

What does exist:

- **Chowmain Marvin Connected Home driver for Control4** — free, manufacturer-sponsored. Auto-imports sashes (blind proxy), skylight shades (blind proxy), skylight LEDs (light proxy), and sash groups.
- **Crestron Home driver** — exists, no public technical detail.

### Control4 driver teardown — done, and it paid off

Downloaded free with no account from Chowmain's CDN:
`https://chowcdn1.nyc3.digitaloceanspaces.com/drivers/control4-marvin-20240530.zip`

The Lua is encrypted (`Code.lua.encrypted`, `encryption="2"`) as expected. But `driver.xml` and the bundled documentation leaked a lot.

**Auth is OAuth 2.0 Device Authorization Grant (RFC 8628).** The agent driver exposes `Verification URL` (a LINK property), `User Code` (STRING), and a `Log In` action; the docs say: click Log In → open the Verification URL → enter the code → log into your Marvin account. This is the "enter this code on another device" flow.

This is *excellent* news for Home Assistant. Device flow is designed exactly for headless clients that can't host a browser redirect, it's what HA uses for several cloud integrations already, and it means we may not need to reverse the mobile app's auth at all — we need the device-authorization endpoint and a `client_id`.

**Other structure revealed:**

- Marvin's API calls devices **"assets"** — the per-device drivers expose `Asset Id`, `Asset Name`, `Current Position`, `Is Open`
- Sashes bind to Control4's `blind` proxy → position is a numeric range, not just the 4 discrete stops your dry contacts use
- Entity types: sash, shade, LED — each with a **group** variant
- Accounts have multiple **houses** (`Select House` dynamic list)
- There's an `Auto Venting` enable/disable command plus events for it
- Actions: `Log In`, `Get Devices`, `Clear Bindings`

**The critical limitation**, quoted from Marvin's own driver documentation:

> Real State may take upto 10 minutes to feedback. The Marvin API though will update changes that have been made through their API immediately though.

And the driver's `Poll Interval` property is a `RANGED_INTEGER` with **minimum 5, maximum 10 minutes** — Chowmain wasn't allowed to poll faster than every 5 minutes, which strongly implies server-side rate limiting.

So: changes *you* make through the API reflect immediately. Changes made physically — someone pressing the on-unit button, rain auto-close firing, an obstruction backing the sash off — take up to 10 minutes to become visible. The docs also warn group state can be out of sync with member state for the same 10 minutes.

---

## Q2: How to reverse-engineer it

### The honest framing on local vs. cloud

You said you'd prefer local control. Based on the evidence, **local control is not achievable without modifying firmware**, and I'd advise against pursuing it. Here's why each local avenue fails:

**DNS redirection to your own MQTT broker.** The obvious trick — point `iot-mch-prd-<cluster>.azure-devices.net` at a local Mosquitto — does not work. The ESP32 validates the TLS server certificate against a pinned/baked-in CA (Microsoft/DigiCert). You cannot present a cert it will accept without first modifying the device's trust store, which means reflashing.

**Firmware extraction.** `esptool.py read_flash` over UART would give you the device's Azure IoT Hub credentials (X.509 client cert or SAS key) — *if* ESP32 flash encryption and secure boot are disabled. Marvin is a large manufacturer shipping a safety-relevant product, so there's a fair chance both are enabled. But even in the best case this only lets you impersonate the device *to Azure*, which is still cloud control, and you'd be fighting the real device for the connection. To get genuine local control you'd have to patch and reflash the firmware, which means:
- Opening the window unit and soldering to UART/JTAG pads
- Real brick risk on an expensive, installed-in-the-wall product
- Losing OTA firmware updates
- Potentially losing rain auto-close and obstruction detection
- Almost certainly voiding warranty

For three windows in bedrooms, that trade is bad.

**BLE.** Worth a 30-minute check, low expectation. Scan with nRF Connect while a window is in pairing mode (hold the on-unit pinhole; note it stays in pairing mode for only 5 minutes). If you see Espressif's standard `wifi_prov_mgr` protocomm GATT service, it's provisioning-only and dead-ends. A custom GATT service with writable characteristics would be a genuine local control channel — but manufacturers rarely ship one.

### Recommended path: cloud API via Android app

This is the standard, well-trodden route and by far the most likely to produce a working integration.

**Important distinction:** you are *not* reversing the MQTT/8883 device protocol. That's device↔IoT Hub, mutually authenticated, and useless to you. You want the **app-facing REST API** — the backend the phone talks to, which in turn issues IoT Hub direct methods or cloud-to-device messages. That API is protected only by user authentication, which you legitimately hold.

**Step 0 — Try the Control4 driver first.** Download the free Chowmain driver, `unzip` the `.c4z`, look for readable Lua. Best case: you skip everything below.

**Step 1 — Static analysis.** Pull `com.marvin.home` (current version 2.1.2, Nov 2025). Decompile with `jadx-gui`. First determine the framework, because it dictates everything downstream:
- `lib/*/libflutter.so` present → **Flutter**. Ignores system proxy and uses its own BoringSSL trust store. You'll need `reFlutter` or a Frida BoringSSL hook. Hardest case.
- `assets/index.android.bundle` → **React Native**. Easiest — often the API base URL and even the auth flow are readable straight out of the JS bundle.
- Neither → native Android/Kotlin. Straightforward in jadx.

Grep the decompiled output for `https://`, `azure`, `b2c`, `login.microsoftonline`, `client_id`, `scope`, `.net/api`. Given the Azure backend, expect **Azure AD B2C** for user auth, though this is unconfirmed — verify, don't assume.

**Step 2 — Dynamic capture.** Run the app in an Android emulator (or a rooted device) with `mitmproxy`:
- Install the mitmproxy CA into the **system** store — `emulator -writable-system` on an AOSP (non-Google-Play) image, or Magisk's `Cert-Fixer` on hardware. A user-store CA alone will not be trusted by a modern app.
- If pinning blocks you, `frida` + `objection`'s `android sslpinning disable`, or a custom hook for the framework you identified in step 1.

Then exercise every function in the app — open to each position, close, read state, trigger an automation, check history — and record the request/response pairs. Pay attention to how the app learns about state *changes* (polling interval vs. a WebSocket/SignalR push channel); that determines whether your HA integration can be event-driven or has to poll.

**Step 3 — Build.** Standard HA custom integration: a small API client library, a `DataUpdateCoordinator`, and `cover` entities (the sash maps cleanly onto `CoverEntity` with your four positions: closed / open 1 / open 2 / open 3), plus `binary_sensor` for lock and rain. Config flow takes Marvin account credentials.

### App teardown — results

APK pulled from the device (`adb pull` from `/data/app/…/com.marvin.home-…/base.apk`), 150 MB, versionCode 81102, `targetSdk 35`.

**Framework: React Native + Expo, Hermes bytecode** (`assets/index.android.bundle`, `libreactnative.so`, `libhermes.so`, HBC version 96). This is the easy case — no Flutter BoringSSL problem.

Statically extracted, no proxy required:

| Finding | Value |
|---|---|
| App API base | `https://azapi.marvin.com/mch-prd/1.0/v1.1/` (endpoint seen: `/messages`) |
| Auth | **Azure AD B2C** — `*.b2clogin.com`, `oauth2/v2.0`, `client_id=` |
| Real-time channel | **SignalR** — confirmed via the `FailedToNegotiateWithServerError` client error string |
| Push registration | `/api/v2/push/updateDeviceToken` |
| BLE | `react-native-ble-plx` (`libBluetoothStateManager.so`, `BleModule`, `BlePlx`) |

**SignalR is the headline.** It confirms the app-facing API has a real-time push channel, which means **the 10-minute state lag is a limitation of the partner API Chowmain was given, not of the platform**. An HA integration built against the app API should be able to get near-real-time state — which resurrects the hybrid architecture.

**Domain model**, from the Hermes string table (parsed properly with `hermes-dec`'s `HBCReader`, 39,996 strings — naive `strings` gives a concatenated blob because the table is unseparated):

- Commands: `CONTROL_SASH`, `CONTROL_SASH_WINDOW`, `CONTROL_LOCK`, `CONTROL_STOP`, `CONTROL_SHADES`, `CONTROL_LIGHTS`, `CONTROL_LED_COLOR`, `CONTROL_LED_INTENSITY`, `CONTROL_CLIC`, `CONTROL_TINTING`, `CONTROL_AUTO_TINT`
- Structure: houses → assets; `ADD_ASSET_TO_HOUSE`, `DELETE_ASSET`, `CREATE_HOUSE`, `ADD_HOUSE_ADMIN`
- Scheduling: `CREATE_SCHEDULE`, `BUILD_SCHEDULE`, `DELETE_SCHEDULE`, `AUTO_SELECT_RAIN`
- Firmware: `CANCEL_FIRMWARE_UPDATING`, `ADVANCED_SETUP_MINIMUM_FIRMWARE`
- Notable: `ASSET_TARGET_STATE_OUTDATED_THRESHOLD` — the app tracks *target* vs *actual* state with a staleness threshold, implying commands are fire-and-confirm rather than synchronous

`CONTROL_STOP` is worth calling out: it means mid-travel stop is supported, which your four dry contacts cannot do.

### Auth configuration (captured)

| | |
|---|---|
| B2C tenant | `marvinwindowsb2c.onmicrosoft.com` |
| `client_id` | `0d117826-a605-4d81-999e-ae67e85de895` |
| Policy | `B2C_1A_AuroraSignInRegister` (custom Identity Experience Framework policy) |
| Branding assets | `stmchb2cprd<cluster>.blob.core.windows.net` |

This is the app's **public** client registration — the same values any copy of the APK contains. It is what an HA integration would authenticate against.

### Capture environment (working)

- AVD `marvin_cap`: Android 15 / API 35, arm64, `google_apis` image, `PlayStore.enabled=false` (so `adb root` works)
- Booted with `-writable-system`; `adb root` + reboot to clear verity
- Android 15 keeps the CA store in the Conscrypt APEX, so `/system/etc/security/cacerts` alone is not enough. Working sequence: tmpfs over `/system/etc/security/cacerts`, repopulate from `/apex/com.android.conscrypt/cacerts`, add the mitmproxy CA (`c8750f0d.0`), fix owner/mode/SELinux label, `mount --bind` over the APEX path, then `nsenter` the same bind into every running process's mount namespace
- Proxy: `settings put global http_proxy 10.0.2.x:8080`; React Native uses OkHttp, which honours it
- **No Play Integrity block** — the app runs normally on the emulator, so rooting the moto is not needed

### Captured API surface (live)

Base: `https://azapi.marvin.com/mch-prd/1.0/`

| Method | Path | Purpose |
|---|---|---|
| GET | `/users` | account + roles |
| GET | `/houses` | house list |
| GET | `/houses/{houseId}` | full state tree — devices, assets, sensors |
| GET | `/defaults` | config defaults |
| GET | `/requeststatus/{houseId}` | async command result polling |
| PUT | `/CreateRegistration` | push notification registration |
| POST | `/v1.1/messages/negotiate` | **SignalR negotiate** |

**Real-time confirmed.** Negotiate returns:

```json
{"url":"https://signr-mch-prd-<cluster>.service.signalr.net/client/?hub=home",
 "accessToken":"..."}
```

**Azure SignalR Service, hub `home`.** So live state push is available to a third-party client — the 10-minute lag is a partner-API limitation, not a platform one.

**Auth:** B2C tenant id `96f9eb15-6ccd-4351-9ca0-94cd182c0bdb`, issuer `https://marvinwindowsb2c.b2clogin.com/{tid}/v2.0/`, `acr: b2c_1a_aurorasigninregister`, access token TTL 1 hour. The SignalR access token is separate and also 1 hour.

**Implementation quirk worth remembering:** the app sends `authorization: Bearer Bearer eyJ…` — a doubled scheme, evidently an app bug the API tolerates. Test whether a single `Bearer` works; if not, replicate the doubling.

### Data model (from `/houses/{houseId}`)

Structure: house → devices → assets, plus `windowStates`, `skylightStates`, `clicStates`, `groupStates`.

Per window asset:

| Field | Example | Maps to |
|---|---|---|
| `sashOpen` | `0` | **current position, percent** |
| `targetSashPercent` | `0` | commanded position |
| `isSashOpen` | `false` | open/closed bool |
| `windowLocked` | `true` | lock state |
| `deviceOnline` | `true` | availability |
| `wiFiRSSI` | `-66` | diagnostic |
| `onUnitFirmwareVersion` | `03.02.00` | diagnostic |

`sashOpen` being a **percentage** confirms full positional control — far beyond the four discrete stops the dry contacts provide.

Also available: `indoorTemperature/Humidity/DewPoint/VOC/AirQuality`, matching `outdoor*` fields, `isRaining`, `sensorDetectedRain`, `eBrakeStatus`, `onBattery`, `errorList`, plus the whole auto-venting config (`autoVentingEnabled`, upper/lower limits per metric, `openConditionMet`, `awayModeIsActive`).

**Sentinel values — important.** Unpopulated numerics come back as type minimums, not null: `indoorTemperature: -1.7976931348623157E+308` (double min) and `isRaining: -2147483648` (int min). An integration must map these to `unavailable`, or you'll get absurd readings in HA.

This user's three units: **Bedroom A**, **Bedroom B**, **Primary Awning** in house "<house>". Reported `wiFiRSSI` of -64/-66 matches the independent UniFi measurements exactly.

### Commands

```json
POST /mch-prd/1.0/commands
{"commands":[{"DeviceId":"Asset_<uuid>-…","Type":"sash",
              "Command":"sash","Value":50,"Schema":"integer"}]}
```

`Value` is the target percentage. It's a batch array, so multiple assets can be commanded in one call. Response echoes per-command status:

```json
{"code":200,"message":"Success","response":[{"type":"sash","deviceId":"Asset_…",
 "command":"sash","code":200,"message":"'sash' Command Sent Successfully to device 'eval3-…'"}]}
```

Devices carry an internal id like `eval3-<serial>` alongside the `Asset_<uuid>` id. There is no separate "stop" command in this capture — issuing a new `sash` value mid-travel redirects the sash, which is how the app implements stop.

### Real-time push — verified end to end

SignalR pushes `AssetUpdated` (full asset JSON) and `HouseGroupStateUpdated`. Observed sequence from one test run:

| Pushes | Event |
|---|---|
| 4–10 | app `sash 50` → `targetSashPercent` 50, unlock, `sashOpen` streams 0 → 18 → 31 → 43 → 50 |
| 11–13 | app `sash 0` then `sash 20`, redirecting mid-travel |
| 14–17 | travel 38 → 25 → 20 |
| 18–24 | **dry-contact close via Zigbee relay** → target 0, `sashOpen` 20 → 7 → 0, then `locked=true` |

Two conclusions:

1. **Position streams live during travel.** HA would show smooth intermediate positions, which is better than most cover integrations manage.
2. **Out-of-band changes push immediately.** The final block was triggered by the dry contact — entirely outside the cloud — and still arrived in real time with the lock confirmation. The device reports its own `targetSashPercent`, so a physical button press or a rain auto-close will behave identically.

This settles the architecture question. The 10-minute lag is a restriction on the partner API only. **An HA integration on the app API gets sub-second bidirectional state.**

### Capture method (for reproducing this)

Static analysis gave the base URL, auth mechanism, and domain model; the proxy capture supplied request/response shapes, the B2C tenant and `client_id`, and the SignalR hub. Setup notes:

- `targetSdk 35` means the app will not trust a user-installed CA, and the moto g play isn't rooted → capture must run on an **emulator** with `emulator -writable-system` and the mitmproxy CA in the system store
- Side-load the pulled APK into the emulator
- Risk: Play Integrity may refuse to run on an emulator. Fallback would be a rooted device with Frida
- Login happens on the emulator, typed by you — I won't handle the credentials

### Parallel path: just ask Marvin

Marvin *sponsored* the Chowmain driver, which means they have a partner API programme and a business reason to say yes. Email `support@connectedhome.marvin.com` (or 888-323-7107) and ask about API access for a personal Home Assistant integration. Costs you one email, and a sanctioned API is dramatically more durable than a reversed one — especially given they've already re-platformed once.

### Legal note

Reverse-engineering for interoperability is generally permitted — DMCA §1201 has an interoperability exemption, and in the EU the Software Directive (Art. 6) explicitly allows decompilation for interoperability. You'd be using your own account against your own devices. Marvin's app ToS may nonetheless prohibit it, so this is a contract question rather than a copyright one. Not legal advice.

---

## Two things worth flagging

### 1. A cloud integration will not be more reliable than your dry contacts

You're moving away from dry contacts because they've been partially unreliable. But consider what the cloud path depends on: your Wi-Fi → your WAN → Azure IoT Hub → Marvin's backend → back down. Every one of those is a failure mode the dry contacts don't have, and command latency goes from milliseconds to hundreds of milliseconds or seconds.

**The dry contacts are the only cloud-independent, internet-outage-proof control path you have.** Before replacing them, it's worth diagnosing *why* they're flaky — Zigbee relay routing, pulse duration vs. what the window controller expects, contact bounce, or wiring. That's likely a smaller and more durable fix.

### 2. Your units' Wi-Fi is marginal

Relevant because a cloud integration inherits this link:

- All three on 2.4 GHz at -57 / -64 / -66 dBm, negotiating only 6.5–7.2 Mbps TX
- `10.0.2.x` shows a **36.5% TX retry rate** (2,378,821 retries / 6,512,662 attempts) and CCQ 30 — that's poor
- Two units share one AP on channel 6; the third is on channel 1 via the *<ap-name>* AP

Also note VLAN 2 is "IoT internet access" — if HA lives on another VLAN, any local approach would need a firewall rule. Not an issue for a cloud integration.

Some of these units support **Ethernet**, which the guide explicitly recommends over Wi-Fi. If any of the three are wired-capable and reachable, that's the single highest-value reliability improvement available, independent of everything else in this document. (Awaken skylights are Wi-Fi-only; casement/awning windows may be Ethernet-capable depending on configuration.)

---

## Best architecture, given all of the above

My initial instinct was "dry contacts for commands, cloud for state feedback." **The 10-minute lag kills that**, at least for the partner API. If HA pulses a relay and wants to confirm the sash actually moved, an API that won't reflect a physical change for ten minutes cannot close that loop. Worth stating plainly rather than leaving the earlier reasoning standing.

What that leaves, in order of preference:

**1. Determine whether the app-facing API is faster.** This is the open question that decides everything. The phone app displays live status and receives push notifications for rain and obstruction events, so *a* real-time channel exists — likely SignalR (Azure's usual choice) or a WebSocket. If the HA integration can subscribe to that, you get immediate bidirectional state and the original hybrid plan works after all. If the app is merely polling the same slow API, it doesn't. **Answering this is the primary goal of the app reverse-engineering.**

**2. Command via the API rather than the relays.** API-initiated changes echo back immediately, so an API-driven cover entity would feel responsive in HA even under the slow-polling model. The cost is that every command traverses your WAN, Azure, and Marvin's backend — and your units' Wi-Fi is already marginal. The gain is true positional control instead of the four discrete stops the dry contacts give you.

**3. Add independent local state sensing.** If neither API is fast enough, the robust answer isn't Marvin's cloud at all — it's a reed switch or similar on each sash reporting into HA over Zigbee. Sub-second local feedback, closes the loop on the dry contacts, depends on nothing Marvin operates. Worth considering seriously: it addresses the actual reliability problem more directly than any cloud integration will.

**Open question:** do the windows currently report *any* position or state back into HA, or are the 4ch relays purely open-loop? That determines whether option 3 is already partly built.

---

## Sources

- [Marvin Connected Home](https://www.marvin.com/solutions/connected-home)
- [Marvin Connected Home Support](https://www.marvin.com/support/connected-home)
- [Marvin Connected Home User Guide (PDF)](https://www.marvin.com/f/1019562/x/bad90766b6/marvin-connected-home-user-guide_19916618.pdf)
- [Marvin Home app download](https://www.marvin.com/support/connected-home/app-download) — [iOS](https://apps.apple.com/us/app/marvin-home/id1496701049), [Android](https://play.google.com/store/apps/details?id=com.marvin.home)
- [Chowmain Marvin Connected Home driver thread (C4 Forums)](https://www.c4forums.com/forums/topic/44097-chowmain-marvin-connected-home-driver-free/)
- [Chowmain releases Marvin ConnectedHome driver (Connected Magazine)](https://connectedmag.com.au/chowmain-soft-releases-marvin-connectedhome-driver-for-control4/)
- [Marvin Connected Home launch (PR Newswire)](https://www.prnewswire.com/news-releases/marvin-unveils-the-future-of-smart-living-with-marvin-connected-home-302071284.html)
- [OUI 48:27:E2 → Espressif Inc.](https://maclookup.app/macaddress/4827e2)
- Live measurements from your UniFi controller and direct TCP scans, 2026-07-26

# Marvin Connected Home for Home Assistant

If you find Marvin Connected Home for Home Assistant useful, consider donating: [![Donate](https://img.shields.io/badge/Donate-PayPal-blue.svg)](https://paypal.me/cb2206)

Control [Marvin Connected Home](https://www.marvin.com/solutions/connected-home) automated windows, skylights and doors from Home Assistant.

**Unofficial.** Marvin publishes no public API. This is built on [`marvin-connected-home`](https://github.com/cb2206/marvin-connected-home), a reverse-engineered client. Marvin has re-platformed the service once already (Google Cloud IoT Core → Azure), so it can change without notice.

## What you get

- **Covers with real 0–100% positioning**, not the handful of stops the dry contacts give you. Position streams live during travel.
- **Sub-second state**, over SignalR — including changes Home Assistant did not make. Press the on-unit button or fire a dry-contact relay and HA sees it immediately.
- **Optional dry-contact fallback**: if the Marvin cloud is unreachable and you have the contacts wired to switch entities, commands automatically fall back to them.
- Lock, rain, obstruction, backup-power and fault sensors; per-component firmware versions; configuration switches; writable dry-contact stop positions.

## Why cloud and not local

The windows are ESP32 devices holding one outbound MQTT/TLS connection to Azure IoT Hub. A full 65,535-port scan of real hardware found **no listening ports**. Local control is not achievable without modifying firmware, which on an installed window means soldering, brick risk, and losing rain auto-close and OTA updates.

The consolation is that the cloud path is genuinely fast — verified sub-second, unlike the ~10-minute polling Marvin's own Control4 driver is limited to.

## Install

**HACS** → Custom repositories → add `https://github.com/cb2206/ha-marvin-connected-home` as an Integration → install → **restart Home Assistant**.

Or copy `custom_components/marvin_connected_home/` into your config directory and restart.

Then **Settings → Devices & services → Add integration → Marvin Connected Home**.

## Signing in

New redirect URIs cannot be registered against Marvin's tenant, so Home Assistant cannot receive the sign-in redirect itself. It asks you to paste back the address you land on:

1. Click the sign-in link in the config flow and sign in, in any browser.
2. You land on a Microsoft page titled **jwt.ms**. It may say it found no token — that is expected.
3. Copy the whole address from the address bar and paste it into the form.

That is the entire flow. No DevTools, and any browser works.

The pasted code is protected by PKCE, so it is useless to anyone who intercepts it — and because the flow asks for `response_mode=fragment`, the code stays in your browser rather than being sent to Microsoft's server at all. Sessions renew automatically; you should not have to repeat this.

<details>
<summary>Why a paste is still needed</summary>

Marvin's app registers `aurora://login/verify`, a mobile-only URI scheme that no browser can open and Safari discards outright — which is what used to make this painful. Azure AD B2C validates `redirect_uri` before it renders a sign-in page, answering `AADB2C90006` for anything unregistered, so the client's registration can be enumerated without signing in. Doing that found `https://jwt.ms` is also registered — Microsoft's own token-inspection page, almost certainly left over from the Azure portal's "Run user flow" default. It is a real page in every browser, which is why the DevTools step is gone.

What is *not* registered, and so is not available: every `http://localhost` and `127.0.0.1` spelling (a loopback listener would remove the copy/paste entirely), the MSAL conventions, and `https://my.home-assistant.io`. Azure AD B2C also does not implement the OAuth device authorization grant — the policy's discovery document advertises no `device_authorization_endpoint` — so the "verification URL plus user code" screen in Marvin's Control4 driver is Chowmain's own relay service, not something this integration can reuse.
</details>

## Entities

One device per window, plus one for the house.

### Per window

| Entity | Notes |
|---|---|
| `cover` | 0–100% position, open/close/stop, live travel state |
| Lock | `on` means **unlocked** — Home Assistant's `lock` device-class convention |
| Rain detected | Per-device rain sensor |
| Obstruction | Only if an e-brake is fitted |
| Running on backup power | Mains lost; running from supercapacitors |
| Fault | On when the window reports one or more errors |
| Closed sensor | Only if enabled on the unit |
| Close when raining / On-window sound / On-window switch LED | Writable configuration |
| Contact position 1/2/3 | Writable dry-contact stop percentages |
| Wi-Fi signal, Target position, Last heartbeat | Diagnostics |
| Firmware (×4–5) | One per component — see below |
| Control path | `Marvin cloud` / `Dry contacts` / `Unavailable` |
| Dry contacts | Summary of the fallback wiring, and where to change it |
| Check for firmware update | Button |
| Reboot | Button — restarts the controller, does not move the sash |
| Recalibrate | Button — **disabled by default**, see below |

A window reports **four independent firmware versions** and they genuinely differ (window control board, on-unit control, rain sensor, motor control board, plus a remote if paired). Collapsing them to one number would mislead anyone debugging board-specific behaviour, so each is its own sensor. The device's `sw_version` is the window control board, since that is the one owners track.

### Per house

Auto venting and its full configuration, plus indoor/outdoor temperature, humidity, dew point, CO₂, VOC, PM2.5, air quality and conditions, an **Open condition met** diagnostic showing whether Marvin's Air Algorithm currently wants the windows open, and **All windows**, a house-wide broadcast cover.

Auto venting is configurable end to end, matching the app:

| Entity | |
|---|---|
| Auto venting | Master switch |
| Open on / Close on temperature | Whether temperature participates |
| Open on / Close on humidity or dew point | Whether moisture participates |
| Auto venting temperature low / high | Thresholds, °F |
| Auto venting humidity low / high | Thresholds, % |
| Auto venting moisture metric | Humidity or dew point |

**Dew-point thresholds must be set in the Marvin app.** Selecting dew point as the metric works from Home Assistant, but Marvin's API uses a different, uncaptured key name for its limits — and this integration does not ship guessed request bodies. The humidity thresholds are fully writable.

Auto venting and its limits update the moment they change in the Marvin app — the cloud pushes them, so there is no polling delay.

**Temperatures are reported in Fahrenheit**, which is what the API returns; Home Assistant converts to whatever unit you have configured. Marvin publishes no unit setting and sells only into the US and Canada.

## Dry-contact fallback

Marvin's windows have momentary dry contacts that work with **no network at all**. That makes them the only control path that survives an internet or cloud outage, so the integration can drive them when the cloud is unreachable.

Entirely optional. Configure it at **Settings → Devices & services → Marvin Connected Home → Configure**, then pick a window. For each contact you name the switch entity it is wired to and the position it drives the sash to.

### Before you configure it

The percentages are pre-filled from the window's own settings, but **the mapping between those settings and the physical terminals is undocumented.** Confirm it against your wiring: fire one relay channel and read the resulting position back from the cover. Five minutes, and it is the difference between the fallback going where you asked and going somewhere else.

Terminals, from Marvin's wiring instructions:

| Terminal | Function |
|---|---|
| 1 | Switch common |
| 2 | Stop sash at current position |
| 3 / 4 / 5 | Open positions 3 / 2 / 1 |
| 6 | Close and lock |

Contacts are **momentary and edge-triggered** — no minimum duration. If your relay pulses itself, set the pulse duration to `0`. Four-wire runs are common and simply have no stop contact.

### What changes while degraded

- Requested positions **snap to the nearest configured stop that moves in the requested direction**, and the cover reports the stop it reached — not the number you asked for. An open request never fires the close relay: with only a close contact wired, "open" refuses rather than shutting the window on you.
- `OPEN`, `SET_POSITION` and `STOP` disappear from the cover's supported features unless your wiring actually supports them — opening needs at least one open-position contact, stopping needs terminal 2.
- **Position and open/closed report `unknown`** unless you configured your own sash contact sensor. A cover claiming 60% while shut is worse than one admitting it does not know.
- The `Control path` sensor reflects reality, so automations can branch on it. Its `degraded_reason` attribute says *why* the cloud path is out — `reauthentication_required`, `cloud_unreachable` or `device_offline` — since the relays behave identically in all three cases but the remedy differs. An optional notification fires on switchover.

A cloud command that fails mid-flight on *connectivity* falls back. One that fails on a per-command auth error or rejection does not — the contacts would mask a real fault rather than fix it. If the saved *session* dies (refresh token expired or revoked), the windows stay controllable through the contacts — automations keep working — while Home Assistant asks you to sign in again, and the switchover notification says the session expired rather than blaming the network.

## Hardware support

| Hardware | Status |
|---|---|
| Modern Automated Casement / Awning | **Verified** against real hardware |
| Awaken skylight | Best-effort — capability-gated, untested |
| Modern Automated Multi-Slide Door | Best-effort, untested |
| CLiC privacy glass | Best-effort, untested |

Entities are created from the capability flags each device reports, so unsupported hardware yields no entity rather than a broken one.

### If your setup has more than this covers

This integration was reverse-engineered from **one account with three casement/awning windows**. Everything here was verified by watching what the Marvin app actually sends — nothing is implemented on a guess, which is the reason some features Marvin's app offers are absent rather than half-working.

That means the gaps are shaped by one person's hardware, not by what the platform can do. If you own Awaken skylights, a Multi-Slide door, CLiC privacy glass, server-side groups, or anything else that produces no entity or a wrong one:

- **Open an issue** with a [diagnostics download](https://www.home-assistant.io/docs/configuration/troubleshooting/#download-diagnostics) (Settings → Devices & services → Marvin Connected Home → ⋮ → Download diagnostics). It is redacted of tokens, email, MAC, IP and UUIDs, and it contains the capability flags and raw state your hardware reports — which is usually enough to add support without owning the hardware.
- **Or open a PR.** `scripts/capture/` has the tooling used to capture the API in the first place: an Android emulator with mitmproxy, a redacting addon, and an analyser that diffs what the app sent against what API.md already documents. RESEARCH.md documents the setup end to end.

Some things Marvin's app does are deliberately not implemented:

| Not implemented | Why |
|---|---|
| Notification preferences | They configure push notifications to the **Marvin phone app**. Home Assistant never receives those, so mirroring the toggles would configure a channel it cannot see. Notify off the rain, obstruction and fault entities instead — better routing, same triggers. |
| Event history | Marvin's feed contains only *window opened / closed / locked / unlocked / sensed rain* — all of which are already entities here, updated sub-second and recorded in the logbook. |
| Server-side groups | Groups have no command endpoint; the app fans out to per-window commands client-side. Home Assistant's areas, labels and groups do the same job without a sync problem. |
| Away mode | Read-only, and only present on an endpoint this integration does not poll. See DESIGN.md. |
| Schedules | Despite constants in the app bundle, no schedule endpoint exists. Home Assistant's automations cover this. |

Full reasoning is in DESIGN.md's non-goals. If you have a use case that changes it, say so in an issue.

### Recalibrate

**Recalibrate drives the sash through its full travel range** — the window opens completely and closes again. That is fine when you are standing in front of it and unwelcome when you are not.

Home Assistant has no per-entity confirmation, so the button ships **disabled by default**. Enable it on the device page when you want it. If you put it on a dashboard, guard it:

```yaml
type: button
entity: button.primary_awning_recalibrate
confirmation:
  text: Recalibrate? The window will open fully and close again.
```

Reboot restarts the window controller and does not move the sash, but it does take the window offline for about a minute.

Both are fire-and-forget: Home Assistant reports success when Marvin's cloud accepts the command, not when the window finishes acting on it. Watch the window's own state to see the outcome.

## Notes

- **Entity IDs pick up the area name.** That is Home Assistant behaviour for devices assigned to an area, not something this integration controls. Assign areas thoughtfully during setup.
- **`All windows` is disabled by default.** It broadcasts to every asset in the house — one API call rather than N, and what the Marvin app's "airflow" control does — but it is easy to press by accident. It reports no position, because averaging windows that disagree would be a lie.
- **Environment sensors may sit `unavailable`.** Marvin returns type minimums (`-1.79e308`, `-2147483648`) rather than null for absent readings; those are mapped to unavailable rather than letting a nonsense value poison your long-term statistics. On accounts where the Air Algorithm is not populating them, they simply stay unavailable.
- **Attributes live behind an entity's More Info dialog**, not in the device list — Home Assistant only renders an entity's state there. The `Dry contacts` sensor's state is written to be useful on its own for that reason.
- **Temperature is assumed to be Celsius.** Where Marvin stores the °C/°F preference has not been found; `/defaults` does not carry it.

## Development

Tests cover the fallback's selection logic and stub the few Home Assistant symbols involved, so they run without a Home Assistant install:

```bash
python -m pytest tests/
```

Never commit `.mitm` capture files — they contain live bearer tokens. `.gitignore` covers them.

The Marvin icon and logo ship in [`custom_components/marvin_connected_home/brand/`](custom_components/marvin_connected_home/brand/), which Home Assistant 2026.3 and later serve directly. Older installs ignore the folder and show the generic placeholder. Regenerate the images from the source SVG with `python scripts/brand/build_brand_assets.py`; see [scripts/brand/README.md](scripts/brand/README.md).

See [API.md](API.md) for the reverse-engineered API reference, [DESIGN.md](DESIGN.md) for entity design and open questions, and [RESEARCH.md](RESEARCH.md) for how the platform was worked out.

## Licence

MIT

# Marvin Connected Home for Home Assistant

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

Marvin's app registers `aurora://login/verify` — a mobile-only URI scheme — and new redirect URIs cannot be registered against their tenant. Home Assistant therefore cannot complete the redirect itself, so the config flow asks you to paste back the URL you land on:

1. Click the sign-in link in the config flow and sign in **in Chrome**.
2. The browser fails to open a page starting `aurora://`. That is expected — copy the whole URL from the address bar.
3. Paste it into the form.

If your browser discards the address (Safari does), open DevTools, enable **Preserve log** on the Network tab, and copy the `Location` header from the final 302.

The pasted code is protected by PKCE, so it is useless to anyone who intercepts it. Sessions renew automatically; you should not have to repeat this.

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
| Closed sensor | Only if enabled on the unit |
| Close when raining / On-window sound / On-window switch LED | Writable configuration |
| Contact position 1/2/3 | Writable dry-contact stop percentages |
| Wi-Fi signal, Target position, Last heartbeat | Diagnostics |
| Firmware (×4–5) | One per component — see below |
| Control path | `Marvin cloud` / `Dry contacts` / `Unavailable` |
| Dry contacts | Summary of the fallback wiring, and where to change it |
| Check for firmware update | Button |

A window reports **four independent firmware versions** and they genuinely differ (window control board, on-unit control, rain sensor, motor control board, plus a remote if paired). Collapsing them to one number would mislead anyone debugging board-specific behaviour, so each is its own sensor. The device's `sw_version` is the window control board, since that is the one owners track.

### Per house

Auto venting, away mode, and indoor/outdoor temperature, humidity, dew point, CO₂, VOC, PM2.5 and conditions. Plus **All windows**, a house-wide broadcast cover.

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

- Requested positions **snap to the nearest configured stop**, and the cover reports the stop it reached — not the number you asked for.
- `SET_POSITION` and `STOP` disappear from the cover's supported features unless your wiring actually supports them.
- **Position and open/closed report `unknown`** unless you configured your own sash contact sensor. A cover claiming 60% while shut is worse than one admitting it does not know.
- The `Control path` sensor reflects reality, so automations can branch on it. An optional notification fires on switchover.

A cloud command that fails on *connectivity* falls back. One that fails on auth or per-command rejection does not — the contacts would mask a real fault rather than fix it.

## Hardware support

| Hardware | Status |
|---|---|
| Modern Automated Casement / Awning | **Verified** against real hardware |
| Awaken skylight | Best-effort — capability-gated, untested |
| Modern Automated Multi-Slide Door | Best-effort, untested |
| CLiC privacy glass | Best-effort, untested |

Entities are created from the capability flags each device reports, so unsupported hardware yields no entity rather than a broken one. If you own something in the untested rows, bug reports are welcome.

**Reboot and Recalibrate are not implemented.** Both exist in the Marvin app, but neither endpoint has been captured, and sending a guessed request body to a live endpoint attached to a motorised window is not a reasonable trade.

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

See [API.md](API.md) for the reverse-engineered API reference, [DESIGN.md](DESIGN.md) for entity design and open questions, and [RESEARCH.md](RESEARCH.md) for how the platform was worked out.

## Licence

MIT

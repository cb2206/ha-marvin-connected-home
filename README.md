# Marvin Connected Home for Home Assistant

Control [Marvin Connected Home](https://www.marvin.com/solutions/connected-home) automated windows, skylights and doors from Home Assistant.

**Unofficial.** Marvin publishes no public API; this is built on [`marvin-connected-home`](https://github.com/cb2206/marvin-connected-home), a reverse-engineered client. Marvin has re-platformed the service once already (Google Cloud IoT Core → Azure), so it can change without notice.

## What you get

- **Covers with real 0–100% positioning**, not the four discrete stops the dry contacts give you. Position streams live during travel.
- **Sub-second state**, over SignalR — including for changes Home Assistant did not make. Press the on-unit button or fire a dry-contact relay and HA sees it immediately.
- Lock, rain detection, obstruction, backup-power and fault sensors.
- Config switches for rain auto-close, on-window sound and the on-window LED.
- Writable dry-contact stop positions, so the hardware switch positions can be retuned from HA.
- All four firmware versions per window, as separate diagnostic sensors.

## Why cloud and not local

The windows are ESP32 devices holding one outbound MQTT/TLS connection to Azure IoT Hub. A full 65,535-port scan of real hardware found **no listening ports**. Local control is not achievable without modifying firmware, which on an installed window means soldering, brick risk, and losing rain auto-close and OTA updates.

The consolation is that the cloud path is genuinely fast — verified sub-second, unlike the ~10-minute polling Marvin's own Control4 driver is limited to.

## Install

**HACS** → Custom repositories → add `https://github.com/cb2206/ha-marvin-connected-home` as an Integration → install → **restart Home Assistant**.

Or copy `custom_components/marvin_connected_home/` into your config directory and restart.

Then **Settings → Devices & Services → Add Integration → Marvin Connected Home**.

## Signing in

Marvin's app registers `aurora://login/verify` — a mobile-only URI scheme — and new redirect URIs cannot be registered against their tenant. Home Assistant therefore cannot complete the redirect itself, so the config flow asks you to paste back the URL you land on:

1. Click the sign-in link in the config flow and sign in **in Chrome**.
2. The browser fails to open a page starting `aurora://`. That is expected — copy the whole URL from the address bar.
3. Paste it into the form.

If your browser discards the address (Safari does), open DevTools, enable **Preserve log** on the Network tab, and copy the `Location` header from the final 302.

The pasted code is protected by PKCE, so it is useless to anyone who intercepts it.

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
- **Environment sensors may sit `unavailable`.** Marvin returns type minimums (`-1.79e308`, `-2147483648`) rather than null for absent readings; the client maps those to unavailable rather than letting a nonsense value poison your long-term statistics. On accounts where the Air Algorithm is not populating them, they simply stay unavailable.
- **The `Lock` binary sensor reads `on` when unlocked.** That is Home Assistant's `lock` device class convention, not a bug.

## Licence

MIT

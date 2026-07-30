---
layout: default
title: Marvin Connected Home for Home Assistant
description: Unofficial Home Assistant integration for Marvin Connected Home automated windows, skylights and doors.
---

# Marvin Connected Home for Home Assistant

An unofficial Home Assistant integration for [Marvin Connected Home](https://www.marvin.com/solutions/connected-home)
automated windows, skylights and doors.

Not affiliated with or endorsed by Marvin. Marvin publishes no public API; this
was reverse-engineered, and can break without notice.

[Integration on GitHub](https://github.com/cb2206/ha-marvin-connected-home) ·
[Python client library](https://github.com/cb2206/marvin-connected-home)

## What it does

Marvin's own sanctioned integrations — the Control4 driver, the dry-contact
terminals behind the trim — give you a handful of fixed stops and, in Control4's
case, roughly ten-minute polling latency. This integration talks to the same
cloud service the Marvin Home app does, which turns out to be considerably more
capable than what the sanctioned paths expose:

- **Covers with real 0–100% positioning.** Position streams live while the
  window is travelling, rather than jumping between preset stops.
- **Sub-second state, including changes Home Assistant did not make.** State
  arrives over SignalR, so pressing the button on the unit or firing a
  dry-contact relay shows up in Home Assistant immediately.
- **Optional dry-contact fallback.** If you have the contacts wired to switch
  entities and the Marvin cloud is unreachable, commands fall back to them
  automatically.
- **Sensors for lock, rain, obstruction, backup power and faults**, plus
  per-component firmware versions and writable dry-contact stop positions.

## Why it is cloud-based

The windows are ESP32 devices that hold one outbound MQTT/TLS connection to
Azure IoT Hub. A full 65,535-port scan of real hardware found no listening
ports at all, so local control is not reachable without modifying firmware —
which on an installed window means soldering, brick risk, and giving up rain
auto-close and OTA updates.

The consolation is that the cloud path is genuinely fast. Sub-second updates
were measured against real hardware.

## Installing

Through [HACS](https://hacs.xyz), add
`https://github.com/cb2206/ha-marvin-connected-home` as a custom Integration
repository, install it, and restart Home Assistant. Then add the integration
from **Settings → Devices & services**.

Full installation and sign-in instructions, including why the config flow asks
you to paste a URL back, are in the
[README](https://github.com/cb2206/ha-marvin-connected-home#readme).

## Further reading

- [API reference](https://github.com/cb2206/ha-marvin-connected-home/blob/main/API.md) — the reverse-engineered endpoints and realtime protocol
- [Design notes](https://github.com/cb2206/ha-marvin-connected-home/blob/main/DESIGN.md) — entity modelling decisions and open questions
- [Research notes](https://github.com/cb2206/ha-marvin-connected-home/blob/main/RESEARCH.md) — how the platform was worked out
- [Issue tracker](https://github.com/cb2206/ha-marvin-connected-home/issues)

Released under the MIT licence.

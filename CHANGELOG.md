# Changelog

## 0.5.0 — 2026-09-04

Requires `marvin-connected-home` 0.5.0 and Home Assistant 2025.8 or newer (the
previous `hacs.json` minimum of 2024.10 was wrong: the options flow already
needed 2025.8).

### Fixed

- Initial setup stored the refresh token from *before* the house listing
  rotated it, leaving a single-use credential on disk.
- A cloud command that timed out fell back to the dry contacts immediately. It
  now confirms the outage with a poll first, so a request that was in fact
  delivered is never doubled by a relay pulse.
- Pulsing a relay whose switch entity was missing or unavailable "succeeded"
  and recorded a position the window never reached. It now raises.
- The realtime feed, once stopped by an authentication failure, was never
  restarted; the next successful poll now restarts it. A push arriving after
  the session died no longer re-advertises the cloud path.
- Pushes that arrived during a poll were overwritten by the older snapshot.
- Auto-venting temperature and humidity limits gained device classes, so they
  convert to the user's display unit.
- The realtime task could outlive a setup that failed part-way.
- Missing `reauth_successful` abort string.

### Changed

- The switchover notification is now a repair issue, cleared automatically
  when the cloud is back.
- Polling tightens from five minutes to one while the SignalR connection is
  down.
- Every user-facing error is translated; icons are declared in `icons.json`;
  windows are nested under the house device; `PARALLEL_UPDATES` declared.
- Uses `entry.runtime_data`; the integration's own logger list now includes
  the library so "enable debug logging" captures it.
- A second test suite (`tests_ha/`) runs the config, reauth and options flows,
  setup and teardown, entities, pushes and diagnostics inside a real Home
  Assistant.

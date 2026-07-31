"""Constants for the Marvin Connected Home integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "marvin_connected_home"

CONF_HOUSE_ID: Final = "house_id"
CONF_REFRESH_TOKEN: Final = "refresh_token"

# --- dry-contact fallback options ---------------------------------------

CONF_FALLBACK: Final = "fallback"
CONF_CLOSE_SWITCH: Final = "close_switch"
CONF_STOP_SWITCH: Final = "stop_switch"
CONF_POSITION_SWITCHES: Final = "position_switches"
CONF_CONTACT_SENSOR: Final = "contact_sensor"
CONF_PULSE_DURATION: Final = "pulse_duration"
CONF_NOTIFY_ON_SWITCHOVER: Final = "notify_on_switchover"
CONF_SWITCH_ENTITY: Final = "entity"
CONF_POSITION: Final = "position"

DEFAULT_PULSE_DURATION: Final = 0.5
"""Contacts are edge-triggered, so this only has to be long enough for the
relay to actuate. Marvin's wiring instructions specify no minimum."""

DEFAULT_NOTIFY_ON_SWITCHOVER: Final = True

# --- control path -------------------------------------------------------

PATH_CLOUD: Final = "cloud"
PATH_DRY_CONTACT: Final = "dry_contact"
PATH_UNAVAILABLE: Final = "unavailable"

# Why the cloud path is out, exposed as the `degraded_reason` attribute on the
# control-path sensor. The relays behave identically in all three cases; the
# *remedy* differs, which is exactly what an automation or a notification
# needs to branch on.
REASON_REAUTH: Final = "reauthentication_required"
REASON_CLOUD: Final = "cloud_unreachable"
REASON_DEVICE: Final = "device_offline"

# --- polling ------------------------------------------------------------

SCAN_INTERVAL_SECONDS: Final = 300
"""Backstop only. State normally arrives over SignalR within a second; this
exists to recover if the socket silently dies."""

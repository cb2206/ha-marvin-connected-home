"""Diagnostics, with redaction.

Diagnostics downloads routinely end up attached to public issue reports, so
anything identifying or credential-bearing is stripped before it leaves.
"""

from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .const import CONF_HOUSE_ID, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import MarvinCoordinator

TO_REDACT = {
    CONF_REFRESH_TOKEN,
    CONF_HOUSE_ID,
    "access_token",
    "accessToken",
    "refresh_token",
    "authorization",
    "email",
    "emailAddress",
    "id",
    "houseId",
    "deviceId",
    "mac",
    "ipaddress",
    "gateway",
    "subnetmask",
    "dnsPrimary",
    "latitude",
    "longitude",
    "zipCode",
    "firstName",
    "lastName",
    "fullNameReversed",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict[str, Any]:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data

    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "house": {
            "asset_count": len(house.assets) if house else 0,
            "auto_venting_enabled": house.auto_venting_enabled if house else None,
            # Which readings carry data, not the values -- enough to diagnose
            # the sentinel behaviour without disclosing anything about the home.
            "environment_populated": sorted(house.environment.populated()) if house else [],
            "preferences": async_redact_data(dict(house.preferences), TO_REDACT)
            if house
            else {},
        },
        "assets": [
            {
                "asset_type": asset.asset_type,
                "device_count": len(asset.devices),
                "board_type": device.board_type,
                "online": device.online,
                "capabilities": {
                    field: getattr(device.capabilities, field)
                    for field in device.capabilities.__dataclass_fields__
                },
                "firmware": device.firmware.as_dict(),
                "sash_position": device.sash_position,
                "target_sash_position": device.target_sash_position,
                "locked": device.locked,
                "wifi_rssi": device.wifi_rssi,
                "connection_type": device.network.connection_type,
                "contact_positions": {
                    field: getattr(device.contact_positions, field)
                    for field in device.contact_positions.__dataclass_fields__
                },
            }
            for asset in (house.assets if house else [])
            if (device := asset.primary) is not None
        ],
    }

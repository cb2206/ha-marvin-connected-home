"""The Marvin Connected Home integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from marvin_connected_home import (
    B2CTokenProvider,
    MarvinAuthError,
    MarvinClient,
    MarvinConnectionError,
    MarvinRealtime,
)

from .const import CONF_HOUSE_ID, CONF_REFRESH_TOKEN, DOMAIN
from .coordinator import MarvinCoordinator

_LOGGER = logging.getLogger(__name__)

PLATFORMS: list[Platform] = [
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.COVER,
    Platform.NUMBER,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)
    provider = B2CTokenProvider(session, refresh_token=entry.data[CONF_REFRESH_TOKEN])

    try:
        await provider.async_refresh()
    except MarvinAuthError as err:
        # The refresh token is dead or revoked. Re-authenticating is the only
        # remedy, so say so rather than retrying forever.
        raise ConfigEntryAuthFailed(str(err)) from err
    except MarvinConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    # Refresh tokens rotate on every use, so the stored one is now stale.
    if provider.refresh_token and provider.refresh_token != entry.data[CONF_REFRESH_TOKEN]:
        hass.config_entries.async_update_entry(
            entry, data={**entry.data, CONF_REFRESH_TOKEN: provider.refresh_token}
        )

    client = MarvinClient(session, provider)
    realtime = MarvinRealtime(session, provider)
    coordinator = MarvinCoordinator(
        hass, entry, client, realtime, entry.data[CONF_HOUSE_ID]
    )

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_realtime()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_reload))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MarvinCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded


async def _async_reload(hass: HomeAssistant, entry: ConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)

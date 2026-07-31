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
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    session = async_get_clientsession(hass)

    def _persist_refresh_token(token: str) -> None:
        """Write every rotation back to the entry, not just the setup-time one.

        B2C rotates the refresh token on each renewal -- roughly hourly -- and
        the old token must be assumed single-use. Persisting only at setup
        leaves a stale credential in storage, which turns any restart after the
        first hour of uptime into a forced re-authentication.
        """
        if token != entry.data.get(CONF_REFRESH_TOKEN):
            hass.config_entries.async_update_entry(
                entry, data={**entry.data, CONF_REFRESH_TOKEN: token}
            )

    provider = B2CTokenProvider(
        session,
        refresh_token=entry.data[CONF_REFRESH_TOKEN],
        on_refresh_token_update=_persist_refresh_token,
    )

    try:
        await provider.async_refresh()
    except MarvinAuthError as err:
        # The refresh token is dead or revoked. Re-authenticating is the only
        # remedy, so say so rather than retrying forever.
        raise ConfigEntryAuthFailed(str(err)) from err
    except MarvinConnectionError as err:
        raise ConfigEntryNotReady(str(err)) from err

    client = MarvinClient(session, provider)
    realtime = MarvinRealtime(session, provider)
    coordinator = MarvinCoordinator(
        hass, entry, client, realtime, entry.data[CONF_HOUSE_ID]
    )

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_realtime()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener here: the options flow is an OptionsFlowWithReload,
    # which reloads the entry itself. Registering a listener as well makes HA
    # raise when the flow finishes, which silently breaks submitting options.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if unloaded:
        coordinator: MarvinCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return unloaded

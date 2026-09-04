"""The Marvin Connected Home integration."""

from __future__ import annotations

import logging

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import device_registry as dr
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

type MarvinConfigEntry = ConfigEntry[MarvinCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: MarvinConfigEntry) -> bool:
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
    # Passing the entry makes the coordinator register its own shutdown on
    # unload -- and on a setup that fails part-way -- so the realtime task
    # started below can never outlive the entry.
    coordinator = MarvinCoordinator(
        hass, entry, client, realtime, entry.data[CONF_HOUSE_ID]
    )

    await coordinator.async_config_entry_first_refresh()
    await coordinator.async_start_realtime()

    # The house is the hub device every window links to, so it has to exist
    # before any platform registers a window against it.
    house = coordinator.data
    dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, coordinator.house_id)},
        manufacturer="Marvin",
        model="Connected Home",
        name=(house.name if house else None) or "Marvin Connected Home",
    )

    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # No update listener here: the options flow is an OptionsFlowWithReload,
    # which reloads the entry itself. Registering a listener as well makes HA
    # raise when the flow finishes, which silently breaks submitting options.
    return True


async def async_unload_entry(hass: HomeAssistant, entry: MarvinConfigEntry) -> bool:
    # The coordinator shuts itself down through the entry's unload hooks.
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

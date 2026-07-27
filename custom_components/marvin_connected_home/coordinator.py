"""Data coordinator.

SignalR is the primary source of state: it delivers updates within a second,
including for changes made outside the cloud entirely (a dry-contact relay or
the on-unit button). Polling is a five-minute backstop in case the socket dies
quietly, not the main path.
"""

from __future__ import annotations

import logging
from datetime import timedelta

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from marvin_connected_home import (
    Asset,
    House,
    MarvinAuthError,
    MarvinClient,
    MarvinError,
    MarvinRealtime,
)

from .const import DOMAIN, SCAN_INTERVAL_SECONDS

_LOGGER = logging.getLogger(__name__)


class MarvinCoordinator(DataUpdateCoordinator[House]):
    """Keeps a House model current from SignalR, with polling as a backstop."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: MarvinClient,
        realtime: MarvinRealtime,
        house_id: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=timedelta(seconds=SCAN_INTERVAL_SECONDS),
            config_entry=entry,
        )
        self.client = client
        self.house_id = house_id
        self._realtime = realtime
        self._unsubscribe: list[callable] = []

    async def _async_update_data(self) -> House:
        try:
            return await self.client.async_get_house(self.house_id)
        except MarvinAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except MarvinError as err:
            raise UpdateFailed(str(err)) from err

    async def async_start_realtime(self) -> None:
        self._unsubscribe.append(self._realtime.on_asset_update(self._handle_asset_update))
        await self._realtime.async_start()

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        await self._realtime.async_stop()
        await super().async_shutdown()

    @callback
    def _handle_asset_update(self, asset: Asset) -> None:
        """Merge a pushed asset into the cached house.

        Replaces in place rather than refetching: the push carries the full
        asset, so a round trip would add latency for no new information.
        """
        if self.data is None:
            return
        for index, existing in enumerate(self.data.assets):
            if existing.asset_id == asset.asset_id:
                self.data.assets[index] = asset
                break
        else:
            self.data.assets.append(asset)
        self.async_set_updated_data(self.data)

    def asset(self, asset_id: str) -> Asset | None:
        if self.data is None:
            return None
        return next((a for a in self.data.assets if a.asset_id == asset_id), None)

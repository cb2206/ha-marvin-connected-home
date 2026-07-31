"""Data coordinator.

SignalR is the primary source of state: it delivers updates within a second,
including for changes made outside the cloud entirely (a dry-contact relay or
the on-unit button). Polling is a five-minute backstop in case the socket dies
quietly, not the main path.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
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
    merge_assets,
)

from .const import (
    CONF_FALLBACK,
    DOMAIN,
    REASON_CLOUD,
    REASON_DEVICE,
    REASON_REAUTH,
    SCAN_INTERVAL_SECONDS,
)
from .fallback import FallbackConfig

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
        self.entry = entry
        self._realtime = realtime
        self._unsubscribe: list[Callable[[], None]] = []
        #: True when the last poll failed on *authentication* rather than
        #: connectivity. The dry contacts work identically either way, but the
        #: remedy differs -- signing in again versus waiting out an outage --
        #: so everything that reports degradation reads this to say which.
        self.auth_failed = False

    # -- control path ---------------------------------------------------

    @property
    def cloud_available(self) -> bool:
        """Whether the Marvin cloud is currently reachable."""
        return self.last_update_success

    def fallback_for(self, asset_id: str) -> FallbackConfig:
        """The user's dry-contact wiring for *asset_id*, if any.

        Read fresh from the entry options each time so an options-flow change
        takes effect without a reload.
        """
        configured = self.entry.options.get(CONF_FALLBACK) or {}
        return FallbackConfig.parse(configured.get(asset_id))

    def device_reachable(self, asset_id: str) -> bool:
        """Whether cloud commands can be expected to reach this asset.

        A device that reports itself offline will accept a command into the void
        -- the API returns success but nothing moves -- so treat that as
        unreachable and let the caller fall back.
        """
        if not self.cloud_available:
            return False
        asset = self.asset(asset_id)
        device = asset.primary if asset else None
        return device is not None and device.online is not False

    def degraded_reason(self, asset_id: str) -> str | None:
        """Why the cloud path is out for *asset_id*, or None while it is up."""
        if self.device_reachable(asset_id):
            return None
        if not self.cloud_available:
            return REASON_REAUTH if self.auth_failed else REASON_CLOUD
        return REASON_DEVICE

    async def _async_update_data(self) -> House:
        try:
            house = await self.client.async_get_house(self.house_id)
        except MarvinAuthError as err:
            self.auth_failed = True
            raise ConfigEntryAuthFailed(str(err)) from err
        except MarvinError as err:
            self.auth_failed = False
            raise UpdateFailed(str(err)) from err
        self.auth_failed = False
        return house

    async def async_start_realtime(self) -> None:
        self._unsubscribe.append(self._realtime.on_asset_update(self._handle_asset_update))
        self._unsubscribe.append(self._realtime.on_house_update(self._handle_house_update))
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

        Merges rather than refetching: the push has carried the full asset in
        every capture so far, so a round trip would add latency for no new
        information. But "observed" is not "guaranteed" -- ``merge_assets``
        preserves cached sections a partial push omits, so a stub payload can
        never flip config switches and contact positions to unknown for the
        five minutes until the next poll.
        """
        if self.data is None:
            return
        for index, existing in enumerate(self.data.assets):
            if existing.asset_id == asset.asset_id:
                self.data.assets[index] = merge_assets(existing, asset)
                break
        else:
            # An asset the polled house has never confirmed. Entities are only
            # created at setup, so appending it would grow state nothing reads;
            # a genuinely new window appears on the next poll and gets entities
            # on the next reload.
            _LOGGER.debug(
                "Ignoring push for unknown asset %s; not in house %s",
                asset.asset_id,
                self.house_id,
            )
            return
        self.async_set_updated_data(self.data)

    @callback
    def _handle_house_update(self, house: House) -> None:
        """Apply a pushed ``PreferencesUpdated`` to the cached house.

        Only the house-level fields are copied across. The payload carries
        ``assets: null`` and ``state: null``, so replacing ``self.data``
        wholesale would drop every window until the next poll -- five minutes
        of unavailable covers from a preference toggle.
        """
        if self.data is None or house.house_id != self.house_id:
            return
        self.data.auto_venting_enabled = house.auto_venting_enabled
        self.data.open_condition_met = house.open_condition_met
        self.data.preferences = house.preferences
        # `away_mode` deliberately not copied: it lives under `state`, which
        # this payload nulls, so a push would always clear it.
        self.async_set_updated_data(self.data)

    def asset(self, asset_id: str) -> Asset | None:
        if self.data is None:
            return None
        return next((a for a in self.data.assets if a.asset_id == asset_id), None)

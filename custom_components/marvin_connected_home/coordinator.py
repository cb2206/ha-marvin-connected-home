"""Data coordinator.

SignalR is the primary source of state: it delivers updates within a second,
including for changes made outside the cloud entirely (a dry-contact relay or
the on-unit button). Polling is a five-minute backstop in case the socket dies
quietly, not the main path -- and tightens to a minute while the socket is
known to be down.
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
    SCAN_INTERVAL_PUSH_DOWN_SECONDS,
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
        self._realtime_started = False
        self._unsubscribe: list[Callable[[], None]] = []
        #: Pushes that arrived while a poll was in flight. The poll's snapshot
        #: predates them, so they are re-applied on top of it rather than lost.
        self._inflight: list[Asset | House] | None = None
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

    # -- polling --------------------------------------------------------

    async def _async_update_data(self) -> House:
        self._inflight = []
        try:
            house = await self.client.async_get_house(self.house_id)
        except MarvinAuthError as err:
            self.auth_failed = True
            raise ConfigEntryAuthFailed(str(err)) from err
        except MarvinError as err:
            self.auth_failed = False
            raise UpdateFailed(str(err)) from err
        finally:
            pushed, self._inflight = self._inflight, None
        self.auth_failed = False

        # A push that landed during the fetch describes a newer state than the
        # snapshot; replay it so a poll can never move a window backwards.
        for update in pushed:
            if isinstance(update, Asset):
                _apply_asset(house, update)
            else:
                _apply_house(house, update)

        # The realtime loop stops itself on an authentication failure and
        # nothing else restarts it. A poll getting through means the
        # credential is good again, so this is the moment to bring it back.
        if self._realtime_started and not self._realtime.running:
            _LOGGER.debug("Realtime is stopped and the cloud is reachable; restarting it")
            await self._realtime.async_start()
        return house

    # -- realtime -------------------------------------------------------

    async def async_start_realtime(self) -> None:
        self._unsubscribe.append(self._realtime.on_asset_update(self._handle_asset_update))
        self._unsubscribe.append(self._realtime.on_house_update(self._handle_house_update))
        self._unsubscribe.append(
            self._realtime.on_connection_change(self._handle_connection_change)
        )
        self._realtime_started = True
        await self._realtime.async_start()

    async def async_shutdown(self) -> None:
        for unsubscribe in self._unsubscribe:
            unsubscribe()
        self._unsubscribe.clear()
        self._realtime_started = False
        await self._realtime.async_stop()
        await super().async_shutdown()

    @callback
    def _handle_connection_change(self, connected: bool) -> None:
        """Adapt polling to whether push is live.

        With the socket down, five minutes between polls is a long time to
        show stale state. Polling every minute while disconnected also means a
        dead session is discovered -- and the reauth flow started -- promptly,
        rather than whenever the next backstop poll happens to fall.
        """
        seconds = SCAN_INTERVAL_SECONDS if connected else SCAN_INTERVAL_PUSH_DOWN_SECONDS
        self.update_interval = timedelta(seconds=seconds)
        if not connected:
            # Debounced by the coordinator, so a flapping socket cannot turn
            # into a poll per flap.
            self.hass.async_create_task(self.async_request_refresh())

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
        if self._inflight is not None:
            self._inflight.append(asset)
        if self.data is None:
            return
        if not _apply_asset(self.data, asset):
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
        self._publish()

    @callback
    def _handle_house_update(self, house: House) -> None:
        """Apply a pushed ``PreferencesUpdated`` to the cached house.

        Only the house-level fields are copied across. The payload carries
        ``assets: null`` and ``state: null``, so replacing ``self.data``
        wholesale would drop every window until the next poll -- five minutes
        of unavailable covers from a preference toggle.
        """
        if house.house_id != self.house_id:
            return
        if self._inflight is not None:
            self._inflight.append(house)
        if self.data is None:
            return
        _apply_house(self.data, house)
        self._publish()

    def _publish(self) -> None:
        """Push the mutated house to entities.

        A push arriving after the session died -- the hub token outlives the
        refresh token -- must not flip ``last_update_success`` back to True,
        or every window would advertise a cloud path that cannot take
        commands. The data is still good, so listeners still get it.
        """
        if self.auth_failed:
            self.async_update_listeners()
        else:
            self.async_set_updated_data(self.data)

    def asset(self, asset_id: str) -> Asset | None:
        if self.data is None:
            return None
        return next((a for a in self.data.assets if a.asset_id == asset_id), None)


def _apply_asset(house: House, asset: Asset) -> bool:
    """Merge *asset* into *house* in place. False if the house lacks it."""
    for index, existing in enumerate(house.assets):
        if existing.asset_id == asset.asset_id:
            house.assets[index] = merge_assets(existing, asset)
            return True
    return False


def _apply_house(house: House, pushed: House) -> None:
    house.auto_venting_enabled = pushed.auto_venting_enabled
    house.open_condition_met = pushed.open_condition_met
    house.preferences = pushed.preferences
    # `away_mode` deliberately not copied: it lives under `state`, which the
    # push nulls, so copying would always clear it.

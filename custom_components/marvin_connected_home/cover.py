"""Cover platform — window sashes, with dry-contact failover."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.components.persistent_notification import async_create as notify
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import MarvinConnectionError, MarvinError

from .const import DOMAIN, PATH_CLOUD, PATH_DRY_CONTACT, PATH_UNAVAILABLE
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity
from .fallback import FallbackConfig, async_pulse, read_contact_sensor

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    entities: list[CoverEntity] = [
        MarvinSashCover(coordinator, asset.asset_id)
        for asset in house.assets
        # Capability-gated: skylights and privacy glass may have no sash at all.
        if (device := asset.primary) is not None and device.capabilities.sash
    ]
    if entities:
        entities.append(MarvinHouseCover(coordinator))
    async_add_entities(entities)


class MarvinSashCover(MarvinAssetEntity, CoverEntity):
    """One window sash.

    Normally driven through the cloud, which gives continuous 0-100% positioning
    and live feedback. If the cloud is unreachable and the user has wired the dry
    contacts, commands fall back to those -- at the cost of only reaching the
    fixed stops, and of losing position feedback unless they also fitted a
    contact sensor.
    """

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_name = None

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, "sash")
        self._notified_degraded = False
        self._last_contact_position: int | None = None

    # -- control path ---------------------------------------------------

    @property
    def _fallback(self) -> FallbackConfig:
        return self.coordinator.fallback_for(self._asset_id)

    @property
    def _degraded(self) -> bool:
        """True when the cloud cannot serve this window."""
        return not self.coordinator.device_reachable(self._asset_id)

    @property
    def control_path(self) -> str:
        if not self._degraded:
            return PATH_CLOUD
        return PATH_DRY_CONTACT if self._fallback.configured else PATH_UNAVAILABLE

    @property
    def available(self) -> bool:
        # Deliberately not calling super(): a window with working dry contacts is
        # still controllable when the cloud is down, so marking it unavailable
        # would hide a control path the user explicitly configured.
        if self.control_path == PATH_UNAVAILABLE:
            return False
        return self.coordinator.last_update_success or self._fallback.configured

    @property
    def supported_features(self) -> CoverEntityFeature:
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
        if not self._degraded:
            return features | CoverEntityFeature.SET_POSITION | CoverEntityFeature.STOP
        # On contacts, positioning still works but snaps to the configured stops,
        # and stop only exists if terminal 2 was wired.
        fallback = self._fallback
        if fallback.stops:
            features |= CoverEntityFeature.SET_POSITION
        if fallback.can_stop:
            features |= CoverEntityFeature.STOP
        return features

    # -- state ----------------------------------------------------------

    @property
    def current_cover_position(self) -> int | None:
        if not self._degraded:
            device = self.device
            return device.sash_position if device else None

        # No cloud means no position feedback. Report the last stop we drove to
        # only when an external contact sensor corroborates it is not closed;
        # otherwise admit we do not know rather than inventing a number.
        closed = read_contact_sensor(self.hass, self._fallback.contact_sensor)
        if closed is None:
            return None
        if closed:
            return 0
        return self._last_contact_position

    @property
    def is_closed(self) -> bool | None:
        if not self._degraded:
            device = self.device
            if device is None or device.sash_open is None:
                return None
            return not device.sash_open
        return read_contact_sensor(self.hass, self._fallback.contact_sensor)

    @property
    def is_opening(self) -> bool:
        return False if self._degraded else self._travelling(opening=True)

    @property
    def is_closing(self) -> bool:
        return False if self._degraded else self._travelling(opening=False)

    def _travelling(self, *, opening: bool) -> bool:
        """Infer travel direction by comparing current and target position.

        Both stream live during movement, so this reflects real motion rather
        than an optimistic guess after a command.
        """
        device = self.device
        if device is None:
            return False
        current, target = device.sash_position, device.target_sash_position
        if current is None or target is None or current == target:
            return False
        return target > current if opening else target < current

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        device = self.device
        attributes: dict[str, Any] = {"control_path": self.control_path}
        if device is not None:
            attributes["target_position"] = device.target_sash_position
            attributes["locked"] = device.locked
        if self._degraded:
            fallback = self._fallback
            attributes["available_positions"] = list(fallback.available_positions)
            attributes["position_is_inferred"] = self.current_cover_position is not None
        return attributes

    # -- commands -------------------------------------------------------

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_move(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_move(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._async_move(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop travel.

        The cloud has no stop command; the Marvin app re-issues the current
        position as the target, and so do we. The dry contacts *do* have a real
        stop terminal, so when degraded that is used directly if wired.
        """
        if self._degraded:
            fallback = self._fallback
            if not fallback.can_stop:
                raise HomeAssistantError(
                    "Cannot stop while the Marvin cloud is unreachable: no stop "
                    "contact is configured for this window"
                )
            await async_pulse(self.hass, fallback.stop_switch, fallback.pulse_duration)
            return

        device = self.device
        if device is None or device.sash_position is None:
            raise HomeAssistantError("Cannot stop: the current sash position is unknown")
        await self._async_move(device.sash_position, allow_fallback=False)

    async def _async_move(self, position: int, *, allow_fallback: bool = True) -> None:
        """Move the sash, preferring the cloud and falling back to contacts."""
        if not self._degraded:
            try:
                await self.coordinator.client.async_set_sash_position(self._asset_id, position)
            except MarvinConnectionError as err:
                # The cloud vanished mid-command. Fall back if we can, since the
                # user's intent was to move the window, not to reach the cloud.
                if not (allow_fallback and self._fallback.configured):
                    raise HomeAssistantError(
                        f"Could not reach the Marvin cloud: {err}"
                    ) from err
                _LOGGER.debug("Cloud command failed, using dry contacts: %s", err)
            except MarvinError as err:
                # An auth or per-command rejection is not a connectivity problem;
                # the contacts would not fix it and might mask a real fault.
                raise HomeAssistantError(f"Failed to move {self.name or 'window'}: {err}") from err
            else:
                self._notified_degraded = False
                return

        if not allow_fallback:
            raise HomeAssistantError("The Marvin cloud is unreachable")
        await self._async_move_via_contacts(position)

    async def _async_move_via_contacts(self, position: int) -> None:
        fallback = self._fallback
        stop = fallback.resolve(position)
        if stop is None:
            raise HomeAssistantError(
                "The Marvin cloud is unreachable and no dry contacts are "
                "configured for this window"
            )

        await async_pulse(self.hass, stop.entity_id, fallback.pulse_duration)
        self._last_contact_position = stop.position
        self.async_write_ha_state()

        if stop.position != position:
            _LOGGER.info(
                "%s: dry contacts offer %s; %d%% requested, drove to %d%%",
                self.entity_id,
                fallback.available_positions,
                position,
                stop.position,
            )

        if fallback.notify_on_switchover and not self._notified_degraded:
            self._notified_degraded = True
            notify(
                self.hass,
                (
                    f"The Marvin cloud is unreachable, so **{self.name or self.entity_id}** "
                    f"was moved using its dry contacts. Only the positions "
                    f"{list(fallback.available_positions)} are available while "
                    "degraded, and position feedback is limited."
                ),
                title="Marvin Connected Home: using dry contacts",
                notification_id=f"{DOMAIN}_{self._asset_id}_degraded",
            )


class MarvinHouseCover(MarvinAssetEntity, CoverEntity):
    """Every sash in the house, as one entity.

    The API accepts a house id in place of an asset id and broadcasts, which is
    what the Marvin app's "airflow" control does. One call beats N.

    Deliberately no position or state: it aggregates windows that can disagree,
    and reporting one number for all of them would be a lie. It is write-only,
    and cloud-only -- a house-wide broadcast has no dry-contact equivalent.

    The only entity here disabled by default, and not for tidiness: one press
    moves every window in the house, which is easy to do by accident and hard
    to undo if it is raining.
    """

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_translation_key = "all_windows"
    _attr_supported_features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE
    _attr_entity_registry_enabled_default = False

    def __init__(self, coordinator: MarvinCoordinator) -> None:
        super().__init__(coordinator, coordinator.house_id, "all_windows")

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success

    @property
    def device_info(self) -> DeviceInfo:
        house = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.house_id)},
            manufacturer="Marvin",
            name=house.name if house else "Marvin Connected Home",
        )

    @property
    def is_closed(self) -> bool | None:
        return None

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_broadcast(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_broadcast(0)

    async def _async_broadcast(self, position: int) -> None:
        try:
            await self.coordinator.client.async_set_sash_position(
                self.coordinator.house_id, position
            )
        except MarvinError as err:
            raise HomeAssistantError(f"House-wide command failed: {err}") from err

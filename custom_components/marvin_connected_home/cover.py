"""Cover platform — window sashes."""

from __future__ import annotations

import logging
from typing import Any

from homeassistant.components.cover import (
    ATTR_POSITION,
    CoverDeviceClass,
    CoverEntity,
    CoverEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity

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
    """One window sash."""

    _attr_device_class = CoverDeviceClass.WINDOW
    _attr_name = None
    _attr_supported_features = (
        CoverEntityFeature.OPEN
        | CoverEntityFeature.CLOSE
        | CoverEntityFeature.SET_POSITION
        | CoverEntityFeature.STOP
    )

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, "sash")

    @property
    def current_cover_position(self) -> int | None:
        device = self.device
        return device.sash_position if device else None

    @property
    def is_closed(self) -> bool | None:
        device = self.device
        if device is None or device.sash_open is None:
            return None
        return not device.sash_open

    @property
    def is_opening(self) -> bool:
        return self._travelling(opening=True)

    @property
    def is_closing(self) -> bool:
        return self._travelling(opening=False)

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
        if device is None:
            return None
        return {"target_position": device.target_sash_position, "locked": device.locked}

    async def async_open_cover(self, **kwargs: Any) -> None:
        await self._async_set_position(100)

    async def async_close_cover(self, **kwargs: Any) -> None:
        await self._async_set_position(0)

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        await self._async_set_position(int(kwargs[ATTR_POSITION]))

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop by re-issuing the live position as the target.

        The API has no stop command; this is how the Marvin app does it. If the
        position is unknown there is nothing meaningful to re-issue, so this
        raises rather than sending a fabricated value to a moving window.
        """
        device = self.device
        if device is None or device.sash_position is None:
            raise HomeAssistantError(
                "Cannot stop: the current sash position is unknown"
            )
        await self._async_set_position(device.sash_position)

    async def _async_set_position(self, position: int) -> None:
        try:
            await self.coordinator.client.async_set_sash_position(self._asset_id, position)
        except MarvinError as err:
            raise HomeAssistantError(f"Failed to move {self.name or 'window'}: {err}") from err


class MarvinHouseCover(MarvinAssetEntity, CoverEntity):
    """Every sash in the house, as one entity.

    The API accepts a house id in place of an asset id and broadcasts, which is
    what the Marvin app's "airflow" control does. One call beats N.

    Deliberately no position or state: it aggregates windows that can disagree,
    and reporting one number for all of them would be a lie. It is write-only.
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

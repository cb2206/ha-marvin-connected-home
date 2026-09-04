"""Shared entity base."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from marvin_connected_home import Asset, Device, House

from .const import DOMAIN
from .coordinator import MarvinCoordinator


class MarvinHouseEntity(CoordinatorEntity[MarvinCoordinator]):
    """Base for entities that belong to the house rather than to one window.

    Auto venting and its limits are house-scoped in the API: there is one set
    of thresholds for every window, not one per window.
    """

    _attr_has_entity_name = True

    def __init__(self, coordinator: MarvinCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.house_id}_{key}"

    @property
    def house(self) -> House | None:
        return self.coordinator.data

    @property
    def device_info(self) -> DeviceInfo:
        house = self.house
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.house_id)},
            manufacturer="Marvin",
            model="Connected Home",
            name=house.name if house else "Marvin Connected Home",
        )


class MarvinAssetEntity(CoordinatorEntity[MarvinCoordinator]):
    """Base for entities belonging to one Marvin asset."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str, key: str) -> None:
        super().__init__(coordinator)
        self._asset_id = asset_id
        self._attr_unique_id = f"{asset_id}_{key}"

    @property
    def asset(self) -> Asset | None:
        return self.coordinator.asset(self._asset_id)

    @property
    def device(self) -> Device | None:
        asset = self.asset
        return asset.primary if asset else None

    @property
    def available(self) -> bool:
        device = self.device
        return super().available and device is not None and device.online is not False

    @property
    def device_info(self) -> DeviceInfo:
        asset = self.asset
        device = self.device
        info = DeviceInfo(
            identifiers={(DOMAIN, self._asset_id)},
            manufacturer="Marvin",
            name=asset.name if asset else None,
            # Windows hang off the house in the device registry, matching how
            # the API models them.
            **self._via_house(),
        )
        if device is None:
            return info

        info["model"] = device.board_type
        # The window control board is the version owners track and the one
        # Marvin's own release notes refer to. The other three are exposed as
        # separate diagnostic sensors rather than being collapsed into this.
        info["sw_version"] = device.firmware.window_control_board
        if device.network.mac:
            info["connections"] = {(CONNECTION_NETWORK_MAC, device.network.mac)}
        return info

    def _via_house(self) -> dict[str, Any]:
        """The ``via_device`` link to the house, in whichever form this HA takes.

        2026.8 deprecated ``via_device`` (an identifier tuple) in favour of
        ``via_device_id`` (a registry id, looked up here). The house device is
        registered before any platform loads, so the lookup always succeeds
        on a current install; older cores get the tuple form they understand.
        """
        house = (DOMAIN, self.coordinator.house_id)
        if not hasattr(dr, "async_get_device_id_by_identifier"):
            return {"via_device": house}
        device_id = dr.async_get_device_id_by_identifier(
            self.hass, house, config_entry_id=self.coordinator.entry.entry_id
        )
        return {"via_device_id": device_id} if device_id else {}

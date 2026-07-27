"""Shared entity base."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from marvin_connected_home import Asset, Device

from .const import DOMAIN
from .coordinator import MarvinCoordinator


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

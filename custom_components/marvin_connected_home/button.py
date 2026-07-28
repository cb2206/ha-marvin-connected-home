"""Button platform — device actions.

Only the firmware-update trigger ships. The Marvin app also offers *Reboot* and
*Recalibrate*, but neither endpoint has been captured, and guessing a request
body against a live endpoint attached to a motorised window is not a reasonable
trade. Recalibrate in particular drives the sash through a full travel cycle, so
when it is added it should carry a confirmation step.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity

CHECK_FIRMWARE = ButtonEntityDescription(
    key="check_firmware",
    translation_key="check_firmware",
    entity_category=EntityCategory.CONFIG,
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    async_add_entities(
        MarvinFirmwareButton(coordinator, asset.asset_id)
        for asset in house.assets
        # Needs the internal device id, so an asset with no device cannot act.
        if asset.primary is not None and asset.primary.device_id
    )


class MarvinFirmwareButton(MarvinAssetEntity, ButtonEntity):
    entity_description = CHECK_FIRMWARE

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, CHECK_FIRMWARE.key)

    async def async_press(self) -> None:
        device = self.device
        if device is None or not device.device_id:
            raise HomeAssistantError("Device id unavailable; cannot request an update")
        try:
            # Note this endpoint takes the internal device id, not the asset id.
            await self.coordinator.client.async_perform_ota(device.device_id)
        except MarvinError as err:
            raise HomeAssistantError(f"Could not request a firmware update: {err}") from err

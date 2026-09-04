"""Button platform — device actions.

All three actions are fire-and-forget: the API acknowledges with a sentence of
plain text and there is no request id to poll. Success here means "the cloud
accepted it", not "the device did it" — the device's own state arrives later
over SignalR.

*Recalibrate* is the one to be careful with: it physically drives the sash
through its full travel range. HA has no per-entity confirmation, so it ships
disabled by default and the README asks for a `confirmation:` block on any
dashboard card that exposes it.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from dataclasses import dataclass
from typing import Any

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from marvin_connected_home import MarvinClient, MarvinError

from . import MarvinConfigEntry
from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity

PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class MarvinButtonDescription(ButtonEntityDescription):
    """Describes a device action.

    `press` takes the **internal device id**, not the asset id — every endpoint
    behind these buttons is addressed by `eval3-...`.
    """

    press: Callable[[MarvinClient, str], Coroutine[Any, Any, None]]
    #: ``exceptions`` key in strings.json used when the action fails.
    error_key: str


BUTTONS: tuple[MarvinButtonDescription, ...] = (
    MarvinButtonDescription(
        key="check_firmware",
        translation_key="check_firmware",
        device_class=ButtonDeviceClass.UPDATE,
        entity_category=EntityCategory.CONFIG,
        press=lambda client, device_id: client.async_perform_ota(device_id),
        error_key="ota_failed",
    ),
    MarvinButtonDescription(
        key="reboot",
        translation_key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press=lambda client, device_id: client.async_reboot_device(device_id),
        error_key="reboot_failed",
    ),
    MarvinButtonDescription(
        key="recalibrate",
        translation_key="recalibrate",
        entity_category=EntityCategory.CONFIG,
        # Disabled by default: an accidental press opens and closes a real
        # window. Owners who want it can enable it deliberately, which is the
        # closest thing HA offers to an "are you sure" at the entity level.
        entity_registry_enabled_default=False,
        press=lambda client, device_id: client.async_recalibrate_device(device_id),
        error_key="calibrate_failed",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: MarvinConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator = entry.runtime_data
    house = coordinator.data
    if house is None:
        return

    async_add_entities(
        MarvinDeviceButton(coordinator, asset.asset_id, description)
        for asset in house.assets
        # Needs the internal device id, so an asset with no device cannot act.
        if asset.primary is not None and asset.primary.device_id
        for description in BUTTONS
    )


class MarvinDeviceButton(MarvinAssetEntity, ButtonEntity):
    entity_description: MarvinButtonDescription

    def __init__(
        self,
        coordinator: MarvinCoordinator,
        asset_id: str,
        description: MarvinButtonDescription,
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        device = self.device
        if device is None or not device.device_id:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="device_id_unavailable"
            )
        try:
            await self.entity_description.press(
                self.coordinator.client, device.device_id
            )
        except MarvinError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key=self.entity_description.error_key,
                translation_placeholders={"error": str(err)},
            ) from err

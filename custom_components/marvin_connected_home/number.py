"""Number platform — dry-contact stop positions.

Each Marvin window exposes three configurable open positions that its physical
dry-contact terminals drive to. Making them writable lets the hardware switch
positions be retuned from Home Assistant.

The mapping from these config keys to physical terminals is *assumed*, not
documented: `hA2Position` is taken to be terminal 5 (Position 1), `hA3Position`
terminal 4, `hA4Position` terminal 3. Verify against your own wiring by firing
one relay channel and reading the resulting position back.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import Device, MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity


@dataclass(frozen=True, kw_only=True)
class MarvinNumberDescription(NumberEntityDescription):
    value_fn: Callable[[Device], int | None]
    #: Index passed to the client, 1-3.
    contact_index: int


NUMBERS: tuple[MarvinNumberDescription, ...] = (
    MarvinNumberDescription(
        key="contact_position_1",
        translation_key="contact_position_1",
        contact_index=1,
        value_fn=lambda d: d.contact_positions.position_1,
    ),
    MarvinNumberDescription(
        key="contact_position_2",
        translation_key="contact_position_2",
        contact_index=2,
        value_fn=lambda d: d.contact_positions.position_2,
    ),
    MarvinNumberDescription(
        key="contact_position_3",
        translation_key="contact_position_3",
        contact_index=3,
        value_fn=lambda d: d.contact_positions.position_3,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    async_add_entities(
        MarvinContactPosition(coordinator, asset.asset_id, description)
        for asset in house.assets
        if (device := asset.primary) is not None and device.capabilities.sash
        for description in NUMBERS
        # Absent on hardware without dry contacts wired or configured.
        if description.value_fn(device) is not None
    )


class MarvinContactPosition(MarvinAssetEntity, NumberEntity):
    entity_description: MarvinNumberDescription

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG
    _attr_entity_registry_enabled_default = False

    def __init__(
        self, coordinator: MarvinCoordinator, asset_id: str, description: MarvinNumberDescription
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.async_set_contact_position(
                self._asset_id, self.entity_description.contact_index, int(value)
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not set contact position: {err}") from err
        await self.coordinator.async_request_refresh()

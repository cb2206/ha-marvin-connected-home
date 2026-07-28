"""Binary sensor platform."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import Capabilities, Device

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity


@dataclass(frozen=True, kw_only=True)
class MarvinBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a Marvin binary sensor."""

    value_fn: Callable[[Device], bool | None]
    supported_fn: Callable[[Capabilities], bool] = lambda _caps: True


SENSORS: tuple[MarvinBinarySensorDescription, ...] = (
    MarvinBinarySensorDescription(
        key="lock",
        translation_key="lock",
        device_class=BinarySensorDeviceClass.LOCK,
        # HA's lock device class is inverted: on means unlocked.
        value_fn=lambda d: None if d.locked is None else not d.locked,
    ),
    MarvinBinarySensorDescription(
        key="rain",
        translation_key="rain",
        device_class=BinarySensorDeviceClass.MOISTURE,
        value_fn=lambda d: d.rain_detected,
    ),
    MarvinBinarySensorDescription(
        key="obstruction",
        translation_key="obstruction",
        device_class=BinarySensorDeviceClass.PROBLEM,
        value_fn=lambda d: d.ebrake_triggered,
        supported_fn=lambda caps: caps.ebrake,
    ),
    MarvinBinarySensorDescription(
        key="on_battery",
        translation_key="on_battery",
        device_class=BinarySensorDeviceClass.BATTERY,
        entity_category=EntityCategory.DIAGNOSTIC,
        # Mains lost; the unit is running from its supercapacitors.
        value_fn=lambda d: d.on_battery,
    ),
    MarvinBinarySensorDescription(
        key="closed_sensor",
        translation_key="closed_sensor",
        device_class=BinarySensorDeviceClass.OPENING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: None if d.closed_sensor is None else not d.closed_sensor,
        supported_fn=lambda caps: caps.closed_sensor,
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
        MarvinBinarySensor(coordinator, asset.asset_id, description)
        for asset in house.assets
        if (device := asset.primary) is not None
        for description in SENSORS
        if description.supported_fn(device.capabilities)
    )


class MarvinBinarySensor(MarvinAssetEntity, BinarySensorEntity):
    entity_description: MarvinBinarySensorDescription

    def __init__(
        self,
        coordinator: MarvinCoordinator,
        asset_id: str,
        description: MarvinBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)

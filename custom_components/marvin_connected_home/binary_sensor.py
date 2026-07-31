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

from marvin_connected_home import Asset, Capabilities, Device

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity, MarvinHouseEntity


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


@dataclass(frozen=True, kw_only=True)
class MarvinAssetBinarySensorDescription(BinarySensorEntityDescription):
    """A sensor sourced from the asset rather than its primary device.

    Errors are reported per *asset*, so they cannot use the device-scoped
    descriptions above.
    """

    value_fn: Callable[[Asset], bool | None]


ASSET_SENSORS: tuple[MarvinAssetBinarySensorDescription, ...] = (
    MarvinAssetBinarySensorDescription(
        key="fault",
        translation_key="fault",
        device_class=BinarySensorDeviceClass.PROBLEM,
        # `errorCount` is an int; anything above zero is a fault. A count of
        # zero is a real "no faults", not an absence, so it must not be
        # collapsed to None.
        value_fn=lambda a: None if a.error_count is None else a.error_count > 0,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    entities: list[BinarySensorEntity] = [
        MarvinBinarySensor(coordinator, asset.asset_id, description)
        for asset in house.assets
        if (device := asset.primary) is not None
        for description in SENSORS
        if description.supported_fn(device.capabilities)
    ]
    entities += [
        MarvinAssetBinarySensor(coordinator, asset.asset_id, description)
        for asset in house.assets
        for description in ASSET_SENSORS
    ]
    entities.append(MarvinOpenConditionSensor(coordinator))
    async_add_entities(entities)


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


class MarvinAssetBinarySensor(MarvinAssetEntity, BinarySensorEntity):
    entity_description: MarvinAssetBinarySensorDescription

    def __init__(
        self,
        coordinator: MarvinCoordinator,
        asset_id: str,
        description: MarvinAssetBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        asset = self.asset
        return None if asset is None else self.entity_description.value_fn(asset)


class MarvinOpenConditionSensor(MarvinHouseEntity, BinarySensorEntity):
    """Whether the Air Algorithm's conditions for opening are currently met.

    Diagnostic, and only meaningful while auto venting is on: it reports what
    Marvin's algorithm thinks, which is the one input you otherwise cannot see
    when trying to work out why a window did or did not open on its own.
    """

    _attr_translation_key = "open_condition_met"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MarvinCoordinator) -> None:
        super().__init__(coordinator, "open_condition_met")

    @property
    def is_on(self) -> bool | None:
        house = self.house
        return None if house is None else house.open_condition_met

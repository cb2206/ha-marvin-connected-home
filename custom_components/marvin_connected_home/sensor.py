"""Sensor platform — diagnostics and house-level climate."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
    CONCENTRATION_PARTS_PER_MILLION,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    EntityCategory,
    UnitOfTemperature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from marvin_connected_home import Device, Environment

from .const import DOMAIN, PATH_CLOUD, PATH_DRY_CONTACT, PATH_UNAVAILABLE
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity


@dataclass(frozen=True, kw_only=True)
class MarvinSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Device], float | str | datetime | None]


DEVICE_SENSORS: tuple[MarvinSensorDescription, ...] = (
    MarvinSensorDescription(
        key="wifi_rssi",
        translation_key="wifi_rssi",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.wifi_rssi,
    ),
    MarvinSensorDescription(
        key="target_position",
        translation_key="target_position",
        native_unit_of_measurement=PERCENTAGE,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.target_sash_position,
    ),
    MarvinSensorDescription(
        key="last_heartbeat",
        translation_key="last_heartbeat",
        device_class=SensorDeviceClass.TIMESTAMP,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _aware(d.last_heartbeat),
    ),
)

#: A window reports four independent firmware versions and they genuinely
#: differ, so collapsing them to one number would mislead anyone debugging
#: board-specific behaviour. All are exposed as diagnostic entities, which
#: keeps them off dashboards while still recording history.
FIRMWARE_SENSORS: tuple[MarvinSensorDescription, ...] = (
    MarvinSensorDescription(
        key="fw_window_control_board",
        translation_key="fw_window_control_board",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware.window_control_board,
    ),
    MarvinSensorDescription(
        key="fw_on_unit",
        translation_key="fw_on_unit",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware.on_unit,
    ),
    MarvinSensorDescription(
        key="fw_rain_sensor",
        translation_key="fw_rain_sensor",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware.rain_sensor,
    ),
    MarvinSensorDescription(
        key="fw_motor_control_board",
        translation_key="fw_motor_control_board",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware.motor_control_board,
    ),
    MarvinSensorDescription(
        key="fw_remote",
        translation_key="fw_remote",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: d.firmware.remote,
    ),
)


@dataclass(frozen=True, kw_only=True)
class MarvinHouseSensorDescription(SensorEntityDescription):
    value_fn: Callable[[Environment], float | str | None]


HOUSE_SENSORS: tuple[MarvinHouseSensorDescription, ...] = (
    MarvinHouseSensorDescription(
        key="indoor_temperature",
        translation_key="indoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_temperature,
    ),
    MarvinHouseSensorDescription(
        key="indoor_humidity",
        translation_key="indoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_humidity,
    ),
    MarvinHouseSensorDescription(
        key="indoor_dew_point",
        translation_key="indoor_dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_dew_point,
    ),
    MarvinHouseSensorDescription(
        key="indoor_co2",
        translation_key="indoor_co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_co2,
    ),
    MarvinHouseSensorDescription(
        key="indoor_voc",
        translation_key="indoor_voc",
        device_class=SensorDeviceClass.VOLATILE_ORGANIC_COMPOUNDS_PARTS,
        native_unit_of_measurement=CONCENTRATION_PARTS_PER_MILLION,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_voc,
    ),
    MarvinHouseSensorDescription(
        key="indoor_pm25",
        translation_key="indoor_pm25",
        device_class=SensorDeviceClass.PM25,
        native_unit_of_measurement=CONCENTRATION_MICROGRAMS_PER_CUBIC_METER,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.indoor_pm25,
    ),
    MarvinHouseSensorDescription(
        key="outdoor_temperature",
        translation_key="outdoor_temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.outdoor_temperature,
    ),
    MarvinHouseSensorDescription(
        key="outdoor_humidity",
        translation_key="outdoor_humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.outdoor_humidity,
    ),
    MarvinHouseSensorDescription(
        key="outdoor_dew_point",
        translation_key="outdoor_dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        value_fn=lambda e: e.outdoor_dew_point,
    ),
    MarvinHouseSensorDescription(
        key="outdoor_conditions",
        translation_key="outdoor_conditions",
        value_fn=lambda e: e.outdoor_conditions,
    ),
)


def _aware(value: datetime | None) -> datetime | None:
    """HA requires timezone-aware datetimes; the API sends naive local ones."""
    if value is None:
        return None
    return value if value.tzinfo else value.replace(tzinfo=UTC)


class MarvinFallbackSummarySensor(MarvinAssetEntity, SensorEntity):
    """Human-readable summary of this window's dry-contact wiring.

    Exists purely for discoverability. The mapping is set in an options flow,
    which is several clicks away and easy to forget the location of, so the
    current configuration is surfaced on the device page itself along with a
    pointer to where it is changed.
    """

    _attr_translation_key = "fallback_summary"
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, "fallback_summary")

    @property
    def available(self) -> bool:
        # Most useful when the window itself is unreachable, so never follow
        # the device's availability.
        return True

    @property
    def native_value(self) -> str:
        fallback = self.coordinator.fallback_for(self._asset_id)
        if not fallback.configured:
            return "Not configured"
        positions = ", ".join(f"{p}%" for p in fallback.available_positions)
        return f"{positions} (no stop)" if not fallback.can_stop else positions

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        fallback = self.coordinator.fallback_for(self._asset_id)
        attributes: dict[str, Any] = {
            "configure_at": (
                "Settings > Devices & services > Marvin Connected Home > "
                "Configure > pick this window"
            )
        }
        if not fallback.configured:
            attributes["status"] = (
                "No dry contacts configured. Cloud outages will leave this "
                "window uncontrollable from Home Assistant."
            )
            return attributes

        if fallback.close_switch:
            attributes["close_and_lock"] = f"{fallback.close_switch} -> 0%"
        for index, stop in enumerate(fallback.stops, start=1):
            attributes[f"open_position_{index}"] = (
                f"{stop.entity_id} -> {stop.position}%"
            )
        attributes["stop"] = fallback.stop_switch or "not wired"
        attributes["sash_contact_sensor"] = (
            fallback.contact_sensor
            or "none - position reports unknown during a cloud outage"
        )
        attributes["pulse_duration_seconds"] = (
            "0 (switch pulses itself)"
            if fallback.pulse_duration == 0
            else fallback.pulse_duration
        )
        attributes["notify_on_switchover"] = fallback.notify_on_switchover
        return attributes


class MarvinControlPathSensor(MarvinAssetEntity, SensorEntity):
    """Which path is currently in use: cloud, dry contacts, or neither.

    Exists regardless of the notification setting so automations can branch on
    degradation, and so the state is visible at a glance rather than only in a
    notification the user may have missed.
    """

    _attr_translation_key = "control_path"
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [PATH_CLOUD, PATH_DRY_CONTACT, PATH_UNAVAILABLE]

    def __init__(self, coordinator: MarvinCoordinator, asset_id: str) -> None:
        super().__init__(coordinator, asset_id, "control_path")

    @property
    def available(self) -> bool:
        # Must stay available precisely when things are degraded -- that is when
        # it carries information.
        return True

    @property
    def native_value(self) -> str:
        if self.coordinator.device_reachable(self._asset_id):
            return PATH_CLOUD
        if self.coordinator.fallback_for(self._asset_id).configured:
            return PATH_DRY_CONTACT
        return PATH_UNAVAILABLE


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    entities: list[SensorEntity] = [
        MarvinDeviceSensor(coordinator, asset.asset_id, description)
        for asset in house.assets
        if (device := asset.primary) is not None
        for description in (*DEVICE_SENSORS, *FIRMWARE_SENSORS)
        # A component that reports no version has none fitted; do not invent an
        # entity that will only ever be unknown.
        if not description.key.startswith("fw_") or description.value_fn(device)
    ]

    for asset in house.assets:
        device = asset.primary
        if device is None or not device.capabilities.sash:
            continue
        entities.append(MarvinControlPathSensor(coordinator, asset.asset_id))
        entities.append(MarvinFallbackSummarySensor(coordinator, asset.asset_id))

    # These are created unconditionally. On accounts where the Air Algorithm is
    # not populating them every reading is a sentinel, which the client maps to
    # None, so they simply report unavailable until data appears.
    entities.extend(
        MarvinHouseSensor(coordinator, description) for description in HOUSE_SENSORS
    )
    async_add_entities(entities)


class MarvinDeviceSensor(MarvinAssetEntity, SensorEntity):
    entity_description: MarvinSensorDescription

    def __init__(
        self, coordinator: MarvinCoordinator, asset_id: str, description: MarvinSensorDescription
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | str | datetime | None:
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)


class MarvinHouseSensor(CoordinatorEntity[MarvinCoordinator], SensorEntity):
    """House-level climate reading."""

    _attr_has_entity_name = True
    entity_description: MarvinHouseSensorDescription

    def __init__(
        self, coordinator: MarvinCoordinator, description: MarvinHouseSensorDescription
    ) -> None:
        super().__init__(coordinator)
        self.entity_description = description
        self._attr_unique_id = f"{coordinator.house_id}_{description.key}"

    @property
    def device_info(self) -> DeviceInfo:
        house = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.house_id)},
            manufacturer="Marvin",
            name=house.name if house else "Marvin Connected Home",
        )

    @property
    def available(self) -> bool:
        return super().available and self.native_value is not None

    @property
    def native_value(self) -> float | str | None:
        house = self.coordinator.data
        return None if house is None else self.entity_description.value_fn(house.environment)

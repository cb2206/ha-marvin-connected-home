"""Switch platform — per-device config and house preferences."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.switch import SwitchEntity, SwitchEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from marvin_connected_home import Device, MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity, MarvinHouseEntity


@dataclass(frozen=True, kw_only=True)
class MarvinSwitchDescription(SwitchEntityDescription):
    value_fn: Callable[[Device], bool | None]
    config_key: str
    #: True when the API stores the inverse of what the switch presents.
    inverted: bool = False


SWITCHES: tuple[MarvinSwitchDescription, ...] = (
    MarvinSwitchDescription(
        key="close_when_raining",
        translation_key="close_when_raining",
        entity_category=EntityCategory.CONFIG,
        config_key="closeWhenRain",
        value_fn=lambda d: d.close_when_raining,
    ),
    MarvinSwitchDescription(
        key="buzzer",
        translation_key="buzzer",
        entity_category=EntityCategory.CONFIG,
        config_key="buzzerDisabled",
        # The API stores "disabled"; the switch presents "sound on", matching
        # the Marvin app's own wording.
        inverted=True,
        value_fn=lambda d: None if d.buzzer_disabled is None else not d.buzzer_disabled,
    ),
    MarvinSwitchDescription(
        key="on_unit_led",
        translation_key="on_unit_led",
        entity_category=EntityCategory.CONFIG,
        config_key="oucLEDEnabled",
        value_fn=lambda d: d.led_enabled,
    ),
)


@dataclass(frozen=True, kw_only=True)
class MarvinPreferenceSwitchDescription(SwitchEntityDescription):
    """An auto-venting condition toggle, stored per house."""

    #: Preference key as spelled when *reading* a house.
    preference_key: str


PREFERENCE_SWITCHES: tuple[MarvinPreferenceSwitchDescription, ...] = (
    MarvinPreferenceSwitchDescription(
        key="temperature_open_if",
        translation_key="temperature_open_if",
        preference_key="temperatureOpenIfEnabled",
    ),
    MarvinPreferenceSwitchDescription(
        key="temperature_close_if",
        translation_key="temperature_close_if",
        preference_key="temperatureCloseIfEnabled",
    ),
    MarvinPreferenceSwitchDescription(
        key="humidity_open_if",
        translation_key="humidity_open_if",
        preference_key="humidityDewPointOpenIfEnabled",
    ),
    MarvinPreferenceSwitchDescription(
        key="humidity_close_if",
        translation_key="humidity_close_if",
        preference_key="humidityDewPointCloseIfEnabled",
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    entities: list[SwitchEntity] = [
        MarvinConfigSwitch(coordinator, asset.asset_id, description)
        for asset in house.assets
        if asset.primary is not None
        for description in SWITCHES
    ]
    entities.append(MarvinAutoVentingSwitch(coordinator))
    entities += [
        MarvinPreferenceSwitch(coordinator, description)
        for description in PREFERENCE_SWITCHES
    ]
    async_add_entities(entities)


class MarvinConfigSwitch(MarvinAssetEntity, SwitchEntity):
    entity_description: MarvinSwitchDescription

    def __init__(
        self, coordinator: MarvinCoordinator, asset_id: str, description: MarvinSwitchDescription
    ) -> None:
        super().__init__(coordinator, asset_id, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        device = self.device
        return None if device is None else self.entity_description.value_fn(device)

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_write(self, desired: bool) -> None:
        stored = not desired if self.entity_description.inverted else desired
        try:
            await self.coordinator.client.async_set_config(
                self._asset_id, self.entity_description.config_key, stored
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not update setting: {err}") from err
        await self.coordinator.async_request_refresh()


class MarvinAutoVentingSwitch(CoordinatorEntity[MarvinCoordinator], SwitchEntity):
    """House-level auto venting.

    This lets Marvin's Air Algorithm open and close windows on its own, so it is
    deliberately a plain control rather than a config-category one -- it has
    real-world effects the user should see in the main entity list.
    """

    _attr_has_entity_name = True
    _attr_translation_key = "auto_venting"

    def __init__(self, coordinator: MarvinCoordinator) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.house_id}_auto_venting"

    @property
    def device_info(self) -> DeviceInfo:
        house = self.coordinator.data
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.house_id)},
            manufacturer="Marvin",
            name=house.name if house else "Marvin Connected Home",
        )

    @property
    def is_on(self) -> bool | None:
        house = self.coordinator.data
        return None if house is None else house.auto_venting_enabled

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.async_set_auto_venting(
                self.coordinator.house_id, enabled
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not change auto venting: {err}") from err
        await self.coordinator.async_request_refresh()


class MarvinPreferenceSwitch(MarvinHouseEntity, SwitchEntity):
    """Whether a metric participates in auto venting.

    Config category rather than a plain control: unlike the auto-venting master
    switch, these only shape *how* the algorithm decides, and do nothing at all
    while auto venting is off.
    """

    entity_description: MarvinPreferenceSwitchDescription

    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: MarvinCoordinator, description: MarvinPreferenceSwitchDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        house = self.house
        if house is None:
            return None
        value = house.preferences.get(self.entity_description.preference_key)
        return value if isinstance(value, bool) else None

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._async_write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._async_write(False)

    async def _async_write(self, enabled: bool) -> None:
        try:
            await self.coordinator.client.async_set_house_preferences(
                self.coordinator.house_id,
                **{self.entity_description.preference_key: enabled},
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not change the venting condition: {err}") from err
        await self.coordinator.async_request_refresh()

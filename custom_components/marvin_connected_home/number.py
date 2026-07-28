"""Number platform — dry-contact stop positions and auto-venting limits.

Each Marvin window exposes three configurable open positions that its physical
dry-contact terminals drive to. Making them writable lets the hardware switch
positions be retuned from Home Assistant, and recording them means a change
is visible in history rather than silently altering what the relays do.

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
from homeassistant.const import PERCENTAGE, EntityCategory, UnitOfTemperature
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from marvin_connected_home import Device, House, MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinAssetEntity, MarvinHouseEntity


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


@dataclass(frozen=True, kw_only=True)
class MarvinLimitDescription(NumberEntityDescription):
    """An auto-venting threshold, stored per house rather than per window."""

    #: Preference key, spelled as it appears when *reading* a house. The client
    #: translates to the write spelling, which differs for temperature.
    preference_key: str


LIMITS: tuple[MarvinLimitDescription, ...] = (
    MarvinLimitDescription(
        key="temperature_lower_limit",
        translation_key="temperature_lower_limit",
        preference_key="temperatureLowerLimit",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        # Marvin's own app offers roughly this span. The API has not been
        # probed for its real bounds, so these are the app's, not the API's.
        native_min_value=35,
        native_max_value=95,
        native_step=1,
    ),
    MarvinLimitDescription(
        key="temperature_upper_limit",
        translation_key="temperature_upper_limit",
        preference_key="temperatureUpperLimit",
        native_unit_of_measurement=UnitOfTemperature.FAHRENHEIT,
        native_min_value=35,
        native_max_value=95,
        native_step=1,
    ),
    MarvinLimitDescription(
        key="humidity_lower_limit",
        translation_key="humidity_lower_limit",
        preference_key="humidityLowerLimit",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
    ),
    MarvinLimitDescription(
        key="humidity_upper_limit",
        translation_key="humidity_upper_limit",
        preference_key="humidityUpperLimit",
        native_unit_of_measurement=PERCENTAGE,
        native_min_value=0,
        native_max_value=100,
        native_step=1,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    house = coordinator.data
    if house is None:
        return

    entities: list[NumberEntity] = [
        MarvinContactPosition(coordinator, asset.asset_id, description)
        for asset in house.assets
        if (device := asset.primary) is not None and device.capabilities.sash
        for description in NUMBERS
        # Absent on hardware without dry contacts wired or configured.
        if description.value_fn(device) is not None
    ]
    entities += [MarvinLimitNumber(coordinator, description) for description in LIMITS]
    async_add_entities(entities)


class MarvinContactPosition(MarvinAssetEntity, NumberEntity):
    entity_description: MarvinNumberDescription

    _attr_native_min_value = 0
    _attr_native_max_value = 100
    _attr_native_step = 1
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

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


class MarvinLimitNumber(MarvinHouseEntity, NumberEntity):
    """One end of an auto-venting threshold.

    These only have an effect while auto venting is on, but they stay writable
    regardless — the app lets you set thresholds before enabling the feature,
    and an entity that rejected writes when a *different* entity was off would
    be worse than one that simply has no effect yet.
    """

    entity_description: MarvinLimitDescription

    _attr_mode = NumberMode.BOX
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(
        self, coordinator: MarvinCoordinator, description: MarvinLimitDescription
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> float | None:
        return _preference(self.house, self.entity_description.preference_key)

    async def async_set_native_value(self, value: float) -> None:
        try:
            await self.coordinator.client.async_set_house_preferences(
                self.coordinator.house_id,
                **{self.entity_description.preference_key: int(value)},
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not set the limit: {err}") from err
        await self.coordinator.async_request_refresh()


def _preference(house: House | None, key: str) -> int | None:
    """Read a numeric preference, mapping Marvin's sentinel to unknown.

    Unset limits come back as int min rather than null — `dewPointUpperLimit`
    is -2147483648 on an account that has never configured one. Left raw, that
    would render as a temperature of minus two billion.
    """
    if house is None:
        return None
    value = house.preferences.get(key)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    return None if value <= -2147483648 else int(value)

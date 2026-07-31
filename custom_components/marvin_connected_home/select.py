"""Select platform — which moisture metric drives auto venting.

Marvin's auto-venting algorithm watches *either* relative humidity or dew
point, never both, and `humidityDewPointToggle` picks which.

**Known gap.** Switching to dew point leaves the thresholds unreachable from
Home Assistant. The humidity limits (`humidityUpperLimit` / `humidityLowerLimit`)
are writable and captured; the dew-point ones (`dewPointUpperLimit` /
`dewPointLowerLimit`) are readable but were never written by the app during
capture, so their write spelling is unknown — and after finding that
`temperatureUpperLimit` writes as `tempUpperLimit`, guessing a key here is not
defensible. Set dew-point thresholds in the Marvin app; this entity still
reports and switches the mode correctly.
"""

from __future__ import annotations

from typing import ClassVar

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from marvin_connected_home import MarvinError

from .const import DOMAIN
from .coordinator import MarvinCoordinator
from .entity import MarvinHouseEntity

PREFERENCE_KEY = "humidityDewPointToggle"

# API value -> Home Assistant option. Both API values are verified: the app was
# observed writing each one.
OPTIONS = {"humidity": "humidity", "dew_point": "dew_point"}


async def async_setup_entry(
    hass: HomeAssistant, entry: ConfigEntry, async_add_entities: AddEntitiesCallback
) -> None:
    coordinator: MarvinCoordinator = hass.data[DOMAIN][entry.entry_id]
    if coordinator.data is None:
        return
    async_add_entities([MarvinMoistureMetricSelect(coordinator)])


class MarvinMoistureMetricSelect(MarvinHouseEntity, SelectEntity):
    _attr_translation_key = "moisture_metric"
    _attr_entity_category = EntityCategory.CONFIG
    _attr_options: ClassVar[list[str]] = list(OPTIONS.values())

    def __init__(self, coordinator: MarvinCoordinator) -> None:
        super().__init__(coordinator, "moisture_metric")

    @property
    def current_option(self) -> str | None:
        house = self.house
        if house is None:
            return None
        value = house.preferences.get(PREFERENCE_KEY)
        # An unrecognised value means Marvin added a third mode; reporting None
        # is better than asserting one of the two we know about.
        return OPTIONS.get(value) if isinstance(value, str) else None

    async def async_select_option(self, option: str) -> None:
        try:
            await self.coordinator.client.async_set_house_preferences(
                self.coordinator.house_id, **{PREFERENCE_KEY: option}
            )
        except MarvinError as err:
            raise HomeAssistantError(f"Could not change the moisture metric: {err}") from err
        await self.coordinator.async_request_refresh()

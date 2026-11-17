"""Entity behavior inside a real Home Assistant: state, pushes, commands."""

from __future__ import annotations

import pytest
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from marvin_connected_home import Asset, MarvinConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from .conftest import ASSET_ID, Cloud, asset_payload


async def test_house_entities_reflect_preferences(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert hass.states.get("switch.test_house_auto_venting").state == "off"
    assert hass.states.get("switch.test_house_open_on_temperature").state == "on"
    assert hass.states.get("select.test_house_auto_venting_moisture_metric").state == "humidity"
    # The test instance is metric: the device class makes Home Assistant
    # convert the Fahrenheit limit to the user's unit instead of showing 54 °C.
    low = hass.states.get("number.test_house_auto_venting_temperature_low")
    assert low.attributes["unit_of_measurement"] == "°C"
    assert float(low.state) == pytest.approx(12.2, abs=0.3)
    indoor = hass.states.get("sensor.test_house_indoor_temperature")
    assert float(indoor.state) == pytest.approx(21.4, abs=0.1)  # 70.5 °F, shown metric
    # A sentinel reading is unavailable, never a number.
    assert hass.states.get("sensor.test_house_outdoor_temperature").state == "unavailable"


async def test_window_entities(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert hass.states.get("binary_sensor.test_window_lock").state == "on"  # unlocked
    assert hass.states.get("binary_sensor.test_window_obstruction").state == "off"
    assert hass.states.get("sensor.test_window_last_heartbeat").state == "2026-07-27T02:19:07+00:00"
    assert hass.states.get("sensor.test_window_control_path").state == "cloud"
    assert hass.states.get("number.test_window_contact_position_2").state == "60"
    assert hass.states.get("switch.test_window_on_window_sound").state == "on"

    registry = er.async_get(hass)
    recalibrate = registry.async_get("button.test_window_recalibrate")
    assert recalibrate is not None and recalibrate.disabled_by is er.RegistryEntryDisabler.INTEGRATION


async def test_push_updates_state_without_a_poll(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert cloud.realtime is not None and cloud.client is not None
    cloud.realtime.push_asset(Asset.parse(asset_payload(sashOpen=80, targetSashPercent=100)))
    await hass.async_block_till_done()
    cover = hass.states.get("cover.test_window")
    assert cover.state == "opening"
    assert cover.attributes["current_position"] == 80
    # The push was merged, not fetched.
    assert cloud.client.async_get_house.await_count == 1


async def test_set_position_goes_to_the_cloud(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    await hass.services.async_call(
        "cover", "set_cover_position", {"entity_id": "cover.test_window", "position": 55}, blocking=True
    )
    assert cloud.client is not None
    cloud.client.async_set_sash_position.assert_awaited_once_with(ASSET_ID, 55)


async def test_transient_failure_is_a_translated_error(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert cloud.client is not None
    cloud.client.async_set_sash_position.side_effect = MarvinConnectionError("timeout")
    with pytest.raises(HomeAssistantError) as excinfo:
        await hass.services.async_call(
            "cover", "close_cover", {"entity_id": "cover.test_window"}, blocking=True
        )
    # No fallback configured, so this must surface -- through strings.json,
    # which is what proves the translation key exists.
    assert "Could not reach the Marvin cloud" in str(excinfo.value)


async def test_config_switch_writes_and_refreshes(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert cloud.client is not None
    await hass.services.async_call(
        "switch", "turn_off", {"entity_id": "switch.test_window_on_window_sound"}, blocking=True
    )
    # "Sound on" off means buzzerDisabled true: the inversion is applied here.
    cloud.client.async_set_config.assert_awaited_once_with(ASSET_ID, "buzzerDisabled", True)


async def test_icons_are_declared(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    """icons.json parses and applies; a malformed file fails silently otherwise."""
    from homeassistant.helpers.icon import async_get_icons

    icons = await async_get_icons(hass, "entity", integrations=["marvin_connected_home"])
    sensor_icons = icons["marvin_connected_home"]["sensor"]
    assert sensor_icons["control_path"]["state"]["dry_contact"] == "mdi:electric-switch"
    # Translated icons are served through the icon API, not state attributes,
    # so loading is the whole check.

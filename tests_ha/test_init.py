"""Entry setup, teardown and failure modes."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from marvin_connected_home import MarvinAuthError, MarvinConnectionError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marvin_connected_home.const import CONF_REFRESH_TOKEN, DOMAIN

from .conftest import ASSET_ID, HOUSE_ID, Cloud


async def test_setup_and_unload(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert loaded_entry.state is ConfigEntryState.LOADED
    assert cloud.realtime is not None and cloud.realtime.starts == 1

    # One device per window, nested under the house.
    devices = {
        identifier: device
        for device in dr.async_entries_for_config_entry(dr.async_get(hass), loaded_entry.entry_id)
        for identifier in device.identifiers
    }
    house = devices[(DOMAIN, HOUSE_ID)]
    window = devices[(DOMAIN, ASSET_ID)]
    assert window.via_device_id == house.id
    assert window.sw_version == "04.09.00"

    cover = hass.states.get("cover.test_window")
    assert cover is not None
    assert cover.state == "open"
    assert cover.attributes["current_position"] == 42
    assert cover.attributes["control_path"] == "cloud"

    assert await hass.config_entries.async_unload(loaded_entry.entry_id)
    await hass.async_block_till_done()
    assert loaded_entry.state is ConfigEntryState.NOT_LOADED
    assert cloud.realtime.stops == 1


async def test_dead_refresh_token_starts_reauth(
    hass: HomeAssistant, cloud: Cloud, config_entry: MockConfigEntry
) -> None:
    cloud.provider_error = MarvinAuthError("AADB2C90080 expired")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_ERROR
    flows = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    assert [flow["context"]["source"] for flow in flows] == ["reauth"]


async def test_unreachable_cloud_retries_setup(
    hass: HomeAssistant, cloud: Cloud, config_entry: MockConfigEntry
) -> None:
    cloud.provider_error = MarvinConnectionError("dns")
    assert not await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    assert config_entry.state is ConfigEntryState.SETUP_RETRY
    assert not hass.config_entries.flow.async_progress_by_handler(DOMAIN)


async def test_rotated_token_is_persisted(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    from .conftest import FakeProvider

    provider = FakeProvider.instances[-1]
    provider.rotate("rt-hourly")
    await hass.async_block_till_done()
    assert loaded_entry.data[CONF_REFRESH_TOKEN] == "rt-hourly"

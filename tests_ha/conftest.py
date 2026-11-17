"""Fixtures for tests against a real Home Assistant.

Unlike ``tests/``, nothing here is stubbed: the config flow, entry setup,
platforms and diagnostics all run inside Home Assistant's own test harness.
Only the cloud is faked, at the library boundary -- the token provider, the
REST client and the realtime feed -- so the models the component consumes are
the real ones, parsed from payloads shaped like the API's.
"""

from __future__ import annotations

from collections.abc import Callable, Generator
from typing import Any, ClassVar
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant
from marvin_connected_home import Asset, House
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marvin_connected_home.const import (
    CONF_HOUSE_ID,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

pytest_plugins = "pytest_homeassistant_custom_component"

HOUSE_ID = "House_00000000-0000-4000-8000-000000000001"
ASSET_ID = "Asset_00000000-0000-4000-8000-000000000002"
DEVICE_ID = "eval3-0123456789ABCDEF01234567"
STORED_TOKEN = "rt-stored"
ROTATED_TOKEN = "rt-rotated"


@pytest.fixture(autouse=True)
def _enable_custom_integrations(enable_custom_integrations: None) -> None:
    """Let the loader find ``custom_components/``."""


def asset_payload(**state: Any) -> dict[str, Any]:
    """One window, shaped like ``GET /houses/{id}/assets`` returns it."""
    device_state = {
        "boardType": "WCB",
        "sashInstalled": True,
        "hasEBrakeInstalled": True,
        "sashOpen": 42,
        "targetSashPercent": 42,
        "isSashOpen": True,
        "windowLocked": False,
        "sensorDetectedRain": False,
        "eBrakeStatus": False,
        "onBattery": False,
        "wiFiRSSI": -60,
        "wcBfirmwareVersion": "04.09.00",
        "onUnitFirmwareVersion": "03.02.00",
        "networkInfo": {"mac": "00:11:22:33:44:55"},
        **state,
    }
    return {
        "id": ASSET_ID,
        "name": "Test Window",
        "assetType": "window",
        "errors": {"errorCount": 0},
        "devices": [
            {
                "id": DEVICE_ID,
                "state": device_state,
                "status": {"deviceOnline": True, "lastHeartbeat": "2026-07-27T02:19:07"},
                "configSettings": {
                    "hA2Position": "20",
                    "hA3Position": "60",
                    "hA4Position": "100",
                    "closeWhenRain": "true",
                    "buzzerDisabled": "false",
                    "oucLEDEnabled": "true",
                },
            }
        ],
    }


def house_payload() -> dict[str, Any]:
    return {
        "id": HOUSE_ID,
        "name": "Test House",
        "preferences": {
            "autoVentingEnabled": False,
            "temperatureLowerLimit": 54,
            "temperatureUpperLimit": 77,
            "humidityLowerLimit": 30,
            "humidityUpperLimit": 60,
            "humidityDewPointToggle": "humidity",
            "temperatureOpenIfEnabled": True,
            "temperatureCloseIfEnabled": True,
            "humidityDewPointOpenIfEnabled": False,
            "humidityDewPointCloseIfEnabled": False,
        },
        "state": {"openConditionMet": False},
        "environment": {"indoorTemperature": 70.5, "outdoorTemperature": -1.7976931348623157e308},
    }


def make_house(**state: Any) -> House:
    return House.parse(house_payload(), assets=[asset_payload(**state)])


class FakeProvider:
    """Stands in for ``B2CTokenProvider`` and records how it was built."""

    instances: ClassVar[list[FakeProvider]] = []

    def __init__(self, session: Any, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.refresh_token: str | None = kwargs.get("refresh_token")
        self.async_refresh = AsyncMock()
        self.async_exchange_code = AsyncMock(side_effect=self._exchange)
        FakeProvider.instances.append(self)

    async def _exchange(self, code: str, verifier: str, redirect_uri: str) -> None:
        self.refresh_token = "rt-exchanged"

    def build_authorization_url(self, redirect_uri: str, challenge: str, *, state: str) -> str:
        return f"https://login.invalid/authorize?state={state}"

    def rotate(self, token: str) -> None:
        """What the real provider does on every redemption."""
        self.refresh_token = token
        if callback := self.kwargs.get("on_refresh_token_update"):
            callback(token)


class FakeClient:
    """Stands in for ``MarvinClient``. Listing houses rotates the token, as the
    real call does -- that is the behavior the config flow has to survive."""

    def __init__(self, session: Any, provider: FakeProvider, **kwargs: Any) -> None:
        self.provider = provider
        self.house = make_house()
        self.houses: list[dict[str, Any]] = [{"id": HOUSE_ID, "name": "Test House"}]
        self.async_get_house = AsyncMock(side_effect=self._get_house)
        self.async_get_houses = AsyncMock(side_effect=self._get_houses)
        self.async_set_sash_position = AsyncMock()
        self.async_set_config = AsyncMock()
        self.async_set_contact_position = AsyncMock()
        self.async_set_house_preferences = AsyncMock()
        self.async_set_auto_venting = AsyncMock()
        self.async_perform_ota = AsyncMock()
        self.async_reboot_device = AsyncMock()
        self.async_recalibrate_device = AsyncMock()

    async def _get_house(self, house_id: str) -> House:
        return self.house

    async def _get_houses(self) -> list[dict[str, Any]]:
        self.provider.rotate(ROTATED_TOKEN)
        return self.houses


class FakeRealtime:
    """Stands in for ``MarvinRealtime``; exposes the callbacks it was given."""

    def __init__(self, session: Any, provider: Any, **kwargs: Any) -> None:
        self.asset_callbacks: list[Callable[[Asset], Any]] = []
        self.house_callbacks: list[Callable[[House], Any]] = []
        self.connection_callbacks: list[Callable[[bool], Any]] = []
        self.starts = 0
        self.stops = 0
        self.running = False
        self.connected = False

    def _subscribe(self, bucket: list[Any], callback: Any) -> Callable[[], None]:
        bucket.append(callback)
        return lambda: bucket.remove(callback)

    def on_asset_update(self, callback: Any) -> Callable[[], None]:
        return self._subscribe(self.asset_callbacks, callback)

    def on_house_update(self, callback: Any) -> Callable[[], None]:
        return self._subscribe(self.house_callbacks, callback)

    def on_connection_change(self, callback: Any) -> Callable[[], None]:
        return self._subscribe(self.connection_callbacks, callback)

    async def async_start(self) -> None:
        self.starts += 1
        self.running = True

    async def async_stop(self) -> None:
        self.stops += 1
        self.running = False

    def push_asset(self, asset: Asset) -> None:
        for callback in list(self.asset_callbacks):
            callback(asset)


class Cloud:
    """Everything the tests can reach into: the fakes the component was given."""

    def __init__(self) -> None:
        self.client: FakeClient | None = None
        self.realtime: FakeRealtime | None = None
        self.provider_error: Exception | None = None


@pytest.fixture
def cloud() -> Generator[Cloud]:
    holder = Cloud()
    FakeProvider.instances.clear()

    def provider(session: Any, **kwargs: Any) -> FakeProvider:
        instance = FakeProvider(session, **kwargs)
        if holder.provider_error is not None:
            instance.async_refresh.side_effect = holder.provider_error
        return instance

    def client(session: Any, provider: FakeProvider, **kwargs: Any) -> FakeClient:
        if holder.client is None:
            holder.client = FakeClient(session, provider, **kwargs)
        else:
            holder.client.provider = provider
        return holder.client

    def realtime(session: Any, provider: Any, **kwargs: Any) -> FakeRealtime:
        holder.realtime = FakeRealtime(session, provider, **kwargs)
        return holder.realtime

    component = "custom_components.marvin_connected_home"
    with (
        patch(f"{component}.B2CTokenProvider", side_effect=provider),
        patch(f"{component}.config_flow.B2CTokenProvider", side_effect=provider),
        patch(f"{component}.MarvinClient", side_effect=client),
        patch(f"{component}.config_flow.MarvinClient", side_effect=client),
        patch(f"{component}.MarvinRealtime", side_effect=realtime),
    ):
        yield holder


@pytest.fixture
def config_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Test House",
        unique_id=HOUSE_ID,
        data={CONF_REFRESH_TOKEN: STORED_TOKEN, CONF_HOUSE_ID: HOUSE_ID},
    )
    entry.add_to_hass(hass)
    return entry


@pytest.fixture
async def loaded_entry(
    hass: HomeAssistant, cloud: Cloud, config_entry: MockConfigEntry
) -> MockConfigEntry:
    assert await hass.config_entries.async_setup(config_entry.entry_id)
    await hass.async_block_till_done()
    return config_entry

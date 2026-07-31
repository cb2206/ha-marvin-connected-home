"""Tests for the coordinator's degradation reporting and push handling.

A dead session and a cloud outage degrade identically — the dry contacts work
either way — but have different remedies, so the coordinator must keep the two
apart: `auth_failed` distinguishes them, and `degraded_reason` folds in the
third case (cloud fine, device offline) for the control-path sensor.

Home Assistant is stubbed in `conftest.py`; the coordinator under test is the
real one, running against a fake client.
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

_coordinator = importlib.import_module("marvin_connected_home.coordinator")
_sensor = importlib.import_module("marvin_connected_home.sensor")
_const = importlib.import_module("marvin_connected_home.const")
_library = importlib.import_module("marvin_connected_home")
_exceptions = importlib.import_module("homeassistant.exceptions")
_update = importlib.import_module("homeassistant.helpers.update_coordinator")

MarvinCoordinator = _coordinator.MarvinCoordinator
MarvinControlPathSensor = _sensor.MarvinControlPathSensor
MarvinAuthError = _library.MarvinAuthError
MarvinConnectionError = _library.MarvinConnectionError
ConfigEntryAuthFailed = _exceptions.ConfigEntryAuthFailed
UpdateFailed = _update.UpdateFailed

HOUSE = "House_test"
ASSET = "Asset_test"


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def async_get_house(self, house_id: str):
        if self.error is not None:
            raise self.error
        return SimpleNamespace(assets=[])


def make_coordinator(
    *,
    error: Exception | None = None,
    online: bool | None = True,
    options: dict | None = None,
) -> MarvinCoordinator:
    coordinator = MarvinCoordinator(
        hass=None,
        entry=SimpleNamespace(options=options or {}),
        client=FakeClient(error),
        realtime=SimpleNamespace(),
        house_id=HOUSE,
    )
    coordinator.data = SimpleNamespace(
        assets=[SimpleNamespace(asset_id=ASSET, primary=SimpleNamespace(online=online))]
    )
    return coordinator


class TestAuthFlag:
    def test_starts_clear(self) -> None:
        assert make_coordinator().auth_failed is False

    def test_auth_failure_sets_it_and_raises_for_reauth(self) -> None:
        coordinator = make_coordinator(error=MarvinAuthError("revoked"))
        with pytest.raises(ConfigEntryAuthFailed):
            asyncio.run(coordinator._async_update_data())
        assert coordinator.auth_failed is True

    def test_connectivity_failure_clears_it(self) -> None:
        """An outage arriving after an expired session must stop claiming the
        problem is authentication."""
        coordinator = make_coordinator(error=MarvinConnectionError("down"))
        coordinator.auth_failed = True
        with pytest.raises(UpdateFailed):
            asyncio.run(coordinator._async_update_data())
        assert coordinator.auth_failed is False

    def test_success_clears_it(self) -> None:
        coordinator = make_coordinator()
        coordinator.auth_failed = True
        asyncio.run(coordinator._async_update_data())
        assert coordinator.auth_failed is False


class TestDegradedReason:
    def test_healthy_is_none(self) -> None:
        assert make_coordinator().degraded_reason(ASSET) is None

    def test_device_offline(self) -> None:
        """Cloud fine, window not: the remedy is at the window, not the router
        or the sign-in form."""
        coordinator = make_coordinator(online=False)
        assert coordinator.degraded_reason(ASSET) == _const.REASON_DEVICE

    def test_cloud_unreachable(self) -> None:
        coordinator = make_coordinator()
        coordinator.last_update_success = False
        assert coordinator.degraded_reason(ASSET) == _const.REASON_CLOUD

    def test_reauthentication_required(self) -> None:
        coordinator = make_coordinator()
        coordinator.last_update_success = False
        coordinator.auth_failed = True
        assert coordinator.degraded_reason(ASSET) == _const.REASON_REAUTH


class TestControlPathSensor:
    """The sensor's state names the path in use; `degraded_reason` says why the
    cloud path is out, so automations can branch on the remedy."""

    def test_cloud(self) -> None:
        sensor = MarvinControlPathSensor(make_coordinator(), ASSET)
        assert sensor.native_value == _const.PATH_CLOUD
        assert sensor.extra_state_attributes == {"degraded_reason": None}

    def test_dry_contact_with_reason(self) -> None:
        coordinator = make_coordinator(
            options={"fallback": {ASSET: {"close_switch": "switch.close"}}}
        )
        coordinator.last_update_success = False
        coordinator.auth_failed = True
        sensor = MarvinControlPathSensor(coordinator, ASSET)
        assert sensor.native_value == _const.PATH_DRY_CONTACT
        assert sensor.extra_state_attributes == {
            "degraded_reason": _const.REASON_REAUTH
        }

    def test_unavailable_without_wiring(self) -> None:
        coordinator = make_coordinator()
        coordinator.last_update_success = False
        sensor = MarvinControlPathSensor(coordinator, ASSET)
        assert sensor.native_value == _const.PATH_UNAVAILABLE
        assert sensor.extra_state_attributes == {
            "degraded_reason": _const.REASON_CLOUD
        }


class TestAssetPush:
    def test_known_asset_is_merged(self) -> None:
        coordinator = make_coordinator()
        pushed = SimpleNamespace(asset_id=ASSET, primary=SimpleNamespace(online=True))
        coordinator._handle_asset_update(pushed)
        assert coordinator.data.assets == [pushed]

    def test_unknown_asset_is_ignored(self) -> None:
        """Entities only exist for polled assets, so appending a stray push
        would grow state nothing reads."""
        coordinator = make_coordinator()
        before = list(coordinator.data.assets)
        coordinator._handle_asset_update(SimpleNamespace(asset_id="Asset_other"))
        assert coordinator.data.assets == before

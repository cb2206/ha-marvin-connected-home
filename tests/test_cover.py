"""Tests for the cover's command orchestration.

The dry-contact *selection* logic is covered in ``test_fallback.py``; what is
pinned here is the decision layer above it, which DESIGN.md flags as never
having run against real relays:

* only a **connectivity** failure falls back to the contacts — an auth or
  per-command rejection raises instead, because relays cannot fix those and
  would mask them;
* a degraded window never touches the cloud client, and a healthy one never
  pulses a relay;
* the switchover notification fires once per degradation, not per command;
* concurrent commands cannot interleave their relay pulses.

Home Assistant is stubbed in `conftest.py`, so this runs without an install.
"""

from __future__ import annotations

import asyncio
import importlib
from types import SimpleNamespace

import pytest

_cover = importlib.import_module("marvin_connected_home.cover")
_exceptions = importlib.import_module("homeassistant.exceptions")
_library = importlib.import_module("marvin_connected_home")
_fallback = importlib.import_module("marvin_connected_home.fallback")

MarvinSashCover = _cover.MarvinSashCover
CoverEntityFeature = importlib.import_module(
    "homeassistant.components.cover"
).CoverEntityFeature
HomeAssistantError = _exceptions.HomeAssistantError
MarvinError = _library.MarvinError
MarvinConnectionError = _library.MarvinConnectionError
FallbackConfig = _fallback.FallbackConfig

ASSET = "Asset_test"
CLOSE = "switch.close"
P1 = "switch.open_20"
P2 = "switch.open_60"


class FakeHass:
    """Records switch service calls; vends states for the contact sensor."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._states: dict[str, SimpleNamespace] = {}
        self.services = SimpleNamespace(async_call=self._async_call)
        self.states = SimpleNamespace(get=self._states.get)

    async def _async_call(self, domain, service, data, blocking=False):
        self.calls.append((service, data["entity_id"]))


class FakeClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[tuple[str, int]] = []

    async def async_set_sash_position(self, asset_id: str, position: int) -> None:
        self.calls.append((asset_id, position))
        if self.error is not None:
            raise self.error


class FakeCoordinator:
    def __init__(
        self,
        *,
        reachable: bool = True,
        fallback_raw: dict | None = None,
        error: Exception | None = None,
        sash_position: int | None = 42,
    ) -> None:
        self.last_update_success = True
        self.auth_failed = False
        self.client = FakeClient(error)
        self._reachable = reachable
        self._fallback_raw = fallback_raw
        self._asset = SimpleNamespace(
            name="Test Window",
            primary=SimpleNamespace(
                online=True,
                sash_position=sash_position,
                target_sash_position=sash_position,
                locked=False,
            ),
        )

    def fallback_for(self, asset_id: str) -> FallbackConfig:
        return FallbackConfig.parse(self._fallback_raw)

    def device_reachable(self, asset_id: str) -> bool:
        return self._reachable

    def asset(self, asset_id: str):
        return self._asset


#: A close contact and two open stops, self-pulsing (no off command, no sleep)
#: so tests stay fast unless a test opts into a real pulse duration.
WIRED = {
    "close_switch": CLOSE,
    "position_switches": [
        {"entity": P1, "position": 20},
        {"entity": P2, "position": 60},
    ],
    "pulse_duration": 0,
}


def make_cover(coordinator: FakeCoordinator) -> tuple[MarvinSashCover, FakeHass]:
    cover = MarvinSashCover(coordinator, ASSET)
    cover.hass = FakeHass()
    cover.entity_id = "cover.test_window"
    return cover, cover.hass


class TestCloudPath:
    def test_command_goes_to_the_cloud_only(self) -> None:
        coordinator = FakeCoordinator(fallback_raw=WIRED)
        cover, hass = make_cover(coordinator)
        asyncio.run(cover.async_set_cover_position(position=55))
        assert coordinator.client.calls == [(ASSET, 55)]
        assert hass.calls == [], "a healthy window must not pulse relays"

    def test_success_clears_stale_contact_position(self) -> None:
        """A later outage must not report a stop driven before this command."""
        coordinator = FakeCoordinator(fallback_raw=WIRED)
        cover, _hass = make_cover(coordinator)
        cover._last_contact_position = 60
        cover._notified_degraded = True
        asyncio.run(cover.async_open_cover())
        assert cover._last_contact_position is None
        assert cover._notified_degraded is False

    def test_stop_reissues_the_current_position(self) -> None:
        coordinator = FakeCoordinator(sash_position=42)
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_stop_cover())
        assert coordinator.client.calls == [(ASSET, 42)]

    def test_stop_with_unknown_position_raises(self) -> None:
        coordinator = FakeCoordinator(sash_position=None)
        cover, _hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError):
            asyncio.run(cover.async_stop_cover())


class TestFailover:
    def test_connection_error_falls_back_to_contacts(self) -> None:
        coordinator = FakeCoordinator(
            fallback_raw=WIRED, error=MarvinConnectionError("down")
        )
        cover, hass = make_cover(coordinator)
        asyncio.run(cover.async_set_cover_position(position=55))
        assert coordinator.client.calls == [(ASSET, 55)], "cloud is tried first"
        assert hass.calls == [("turn_on", P2)], "55% snaps to the 60% stop"
        assert cover._last_contact_position == 60

    def test_connection_error_without_fallback_raises(self) -> None:
        coordinator = FakeCoordinator(error=MarvinConnectionError("down"))
        cover, hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError):
            asyncio.run(cover.async_open_cover())
        assert hass.calls == []

    def test_non_connectivity_error_never_falls_back(self) -> None:
        """Auth and per-command rejections are not connectivity problems; the
        relays would not fix them and might mask a real fault."""
        coordinator = FakeCoordinator(fallback_raw=WIRED, error=MarvinError("rejected"))
        cover, hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError):
            asyncio.run(cover.async_close_cover())
        assert hass.calls == [], "the close relay must stay untouched"

    def test_degraded_window_skips_the_cloud(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        assert coordinator.client.calls == []
        assert hass.calls == [("turn_on", CLOSE)]

    def test_degraded_open_with_close_only_wiring_refuses(self) -> None:
        """Firing the close relay for an open request would move the window in
        the opposite direction from the one the user asked for."""
        coordinator = FakeCoordinator(
            reachable=False, fallback_raw={"close_switch": CLOSE, "pulse_duration": 0}
        )
        cover, hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError):
            asyncio.run(cover.async_open_cover())
        assert hass.calls == []

    def test_degraded_stop_uses_the_stop_terminal(self) -> None:
        coordinator = FakeCoordinator(
            reachable=False,
            fallback_raw={**WIRED, "stop_switch": "switch.stop"},
        )
        cover, hass = make_cover(coordinator)
        asyncio.run(cover.async_stop_cover())
        assert hass.calls == [("turn_on", "switch.stop")]

    def test_degraded_stop_without_stop_terminal_raises(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError):
            asyncio.run(cover.async_stop_cover())
        assert hass.calls == []


class TestNotification:
    def test_fires_once_per_degradation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notifications: list[str] = []
        monkeypatch.setattr(
            _cover, "notify", lambda hass, message, **kwargs: notifications.append(message)
        )
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, _hass = make_cover(coordinator)

        asyncio.run(cover.async_close_cover())
        asyncio.run(cover.async_open_cover())
        assert len(notifications) == 1, "second command during one outage is silent"

        # Cloud recovers, then fails again: a fresh degradation notifies again.
        coordinator._reachable = True
        asyncio.run(cover.async_open_cover())
        coordinator._reachable = False
        asyncio.run(cover.async_close_cover())
        assert len(notifications) == 2

    def test_dead_session_is_not_blamed_on_the_network(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Telling the user the cloud is unreachable when their session expired
        sends them to check the router instead of signing in again."""
        notifications: list[str] = []
        monkeypatch.setattr(
            _cover, "notify", lambda hass, message, **kwargs: notifications.append(message)
        )
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        coordinator.auth_failed = True
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        assert len(notifications) == 1
        assert "session has expired" in notifications[0]
        assert "Sign in again" in notifications[0]
        assert "unreachable" not in notifications[0]

    def test_honours_the_opt_out(self, monkeypatch: pytest.MonkeyPatch) -> None:
        notifications: list[str] = []
        monkeypatch.setattr(
            _cover, "notify", lambda hass, message, **kwargs: notifications.append(message)
        )
        coordinator = FakeCoordinator(
            reachable=False, fallback_raw={**WIRED, "notify_on_switchover": False}
        )
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        assert notifications == []


class TestSupportedFeatures:
    def test_cloud_offers_everything(self) -> None:
        cover, _hass = make_cover(FakeCoordinator())
        assert cover.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
            | CoverEntityFeature.STOP
        )

    def test_degraded_close_only_offers_close_only(self) -> None:
        coordinator = FakeCoordinator(
            reachable=False, fallback_raw={"close_switch": CLOSE}
        )
        cover, _hass = make_cover(coordinator)
        assert cover.supported_features == CoverEntityFeature.CLOSE

    def test_degraded_open_stops_enable_open_and_position(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, _hass = make_cover(coordinator)
        assert cover.supported_features == (
            CoverEntityFeature.OPEN
            | CoverEntityFeature.CLOSE
            | CoverEntityFeature.SET_POSITION
        )

    def test_stop_requires_the_stop_terminal(self) -> None:
        coordinator = FakeCoordinator(
            reachable=False, fallback_raw={**WIRED, "stop_switch": "switch.stop"}
        )
        cover, _hass = make_cover(coordinator)
        assert cover.supported_features & CoverEntityFeature.STOP


class TestPulseSerialisation:
    def test_concurrent_commands_do_not_interleave_pulses(self) -> None:
        """Two overlapping commands must produce two complete on/off pulses in
        sequence, never on,on,off,off across different relays."""
        raw = {**WIRED, "pulse_duration": 0.02}
        coordinator = FakeCoordinator(reachable=False, fallback_raw=raw)
        cover, hass = make_cover(coordinator)

        async def race() -> None:
            await asyncio.gather(
                cover.async_set_cover_position(position=20),
                cover.async_set_cover_position(position=60),
            )

        asyncio.run(race())
        services = [service for service, _entity in hass.calls]
        assert services == ["turn_on", "turn_off", "turn_on", "turn_off"]
        # Each pulse's off releases the same relay its on closed.
        assert hass.calls[0][1] == hass.calls[1][1]
        assert hass.calls[2][1] == hass.calls[3][1]

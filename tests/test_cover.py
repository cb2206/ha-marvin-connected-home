"""Tests for the cover's command orchestration.

The dry-contact *selection* logic is covered in ``test_fallback.py``; what is
pinned here is the decision layer above it, which DESIGN.md flags as never
having run against real relays:

* only a **connectivity** failure falls back to the contacts — an auth or
  per-command rejection raises instead, because relays cannot fix those and
  would mask them;
* a degraded window never touches the cloud client, and a healthy one never
  pulses a relay;
* a connectivity failure mid-command is *confirmed* by a poll before any relay
  moves, so a timed-out command that was in fact delivered is not doubled;
* the switchover repair issue is raised once per degradation, not per command;
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
_issues = importlib.import_module("homeassistant.helpers.issue_registry")

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
    """Records switch service calls; vends states for the contact sensor.

    Every relay switch exists and is off unless a test says otherwise, since a
    pulse now refuses to fire at an entity that is missing or unavailable.
    """

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self._states: dict[str, SimpleNamespace] = {}
        self.services = SimpleNamespace(async_call=self._async_call)
        self.states = SimpleNamespace(get=self._get_state)

    def _get_state(self, entity_id: str) -> SimpleNamespace | None:
        if entity_id in self._states:
            return self._states[entity_id]
        return SimpleNamespace(state="off") if entity_id.startswith("switch.") else None

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
        outage: bool | None = None,
    ) -> None:
        self.last_update_success = True
        self.auth_failed = False
        self.client = FakeClient(error)
        # What a confirming poll finds. Defaults to "the cloud is really down"
        # whenever the client is set to fail with a connection error.
        self._outage = isinstance(error, MarvinConnectionError) if outage is None else outage
        self.refreshes = 0
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

    async def async_refresh(self) -> None:
        self.refreshes += 1
        self.last_update_success = not self._outage
        if self._outage:
            self._reachable = False

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
        assert coordinator.refreshes == 1, "the outage is confirmed by a poll first"
        assert hass.calls == [("turn_on", P2)], "55% snaps to the 60% stop"
        assert cover._last_contact_position == 60

    def test_transient_failure_with_cloud_up_does_not_fall_back(self) -> None:
        """A timed-out command may have been delivered. If the confirming poll
        gets through, the cloud is up and a relay pulse would hand the sash a
        second, conflicting target -- so this must raise instead."""
        coordinator = FakeCoordinator(
            fallback_raw=WIRED, error=MarvinConnectionError("timeout"), outage=False
        )
        cover, hass = make_cover(coordinator)
        with pytest.raises(HomeAssistantError) as excinfo:
            asyncio.run(cover.async_set_cover_position(position=55))
        assert excinfo.value.translation_key == "cloud_command_failed"
        assert hass.calls == []
        assert cover._last_contact_position is None

    def test_missing_relay_entity_raises_instead_of_pretending(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, hass = make_cover(coordinator)
        hass._states[CLOSE] = SimpleNamespace(state="unavailable")
        with pytest.raises(HomeAssistantError) as excinfo:
            asyncio.run(cover.async_close_cover())
        assert excinfo.value.translation_key == "relay_unavailable"
        assert hass.calls == []
        assert cover._last_contact_position is None

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


class TestRepairIssue:
    @pytest.fixture(autouse=True)
    def _clear_issues(self) -> None:
        _issues.issues.clear()

    def test_raised_once_per_degradation(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, _hass = make_cover(coordinator)

        asyncio.run(cover.async_close_cover())
        first = dict(_issues.issues)
        asyncio.run(cover.async_open_cover())
        assert len(_issues.issues) == 1, "second command during one outage is silent"
        assert _issues.issues == first

        # Cloud recovers: the issue clears on the next successful command.
        coordinator._reachable = True
        asyncio.run(cover.async_open_cover())
        assert _issues.issues == {}
        # And a fresh degradation raises it again.
        coordinator._reachable = False
        asyncio.run(cover.async_close_cover())
        assert len(_issues.issues) == 1

    def test_clears_when_a_state_push_shows_the_cloud_back(self) -> None:
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        assert len(_issues.issues) == 1
        coordinator._reachable = True
        cover._handle_coordinator_update()
        assert _issues.issues == {}

    def test_dead_session_is_not_blamed_on_the_network(self) -> None:
        """Telling the user the cloud is unreachable when their session expired
        sends them to check the router instead of signing in again."""
        coordinator = FakeCoordinator(reachable=False, fallback_raw=WIRED)
        coordinator.auth_failed = True
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        (issue,) = _issues.issues.values()
        assert issue["translation_key"] == "using_dry_contacts_reauth"
        assert issue["translation_placeholders"]["positions"] == "0%, 20%, 60%"

    def test_honours_the_opt_out(self) -> None:
        coordinator = FakeCoordinator(
            reachable=False, fallback_raw={**WIRED, "notify_on_switchover": False}
        )
        cover, _hass = make_cover(coordinator)
        asyncio.run(cover.async_close_cover())
        assert _issues.issues == {}


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


class TestPulseSerialization:
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

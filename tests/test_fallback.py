"""Tests for dry-contact fallback selection logic.

These stub the few Home Assistant symbols `fallback` imports so the pure
decision logic can be exercised without a Home Assistant install, and without
any hardware or a simulated cloud outage.

    python -m pytest tests/
"""

from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

# --- minimal homeassistant stubs -----------------------------------------

_const = types.ModuleType("homeassistant.const")
_const.ATTR_ENTITY_ID = "entity_id"
_const.STATE_ON = "on"
_const.STATE_UNAVAILABLE = "unavailable"
_const.STATE_UNKNOWN = "unknown"

_core = types.ModuleType("homeassistant.core")


class _HomeAssistant:  # noqa: D101 - stub
    pass


_core.HomeAssistant = _HomeAssistant

_ha = types.ModuleType("homeassistant")
_ha.__path__ = []  # let `homeassistant.const` resolve as a submodule
sys.modules.setdefault("homeassistant", _ha)
sys.modules.setdefault("homeassistant.const", _const)
sys.modules.setdefault("homeassistant.core", _core)

# Stand in for the real package so importing a submodule does not execute
# __init__.py, which pulls in the whole Home Assistant framework.
_COMPONENT = Path(__file__).resolve().parent.parent / "custom_components" / "marvin_connected_home"
_pkg = types.ModuleType("marvin_connected_home")
_pkg.__path__ = [str(_COMPONENT)]
sys.modules.setdefault("marvin_connected_home", _pkg)

import importlib  # noqa: E402

_fallback = importlib.import_module("marvin_connected_home.fallback")
ContactStop = _fallback.ContactStop
FallbackConfig = _fallback.FallbackConfig

CLOSE = "switch.window_closed"
P1 = "switch.window_open_1"
P2 = "switch.window_open_2"
P3 = "switch.window_open_3"


def build(**overrides) -> FallbackConfig:
    """A typical 4-wire install: close plus three open stops."""
    raw = {
        "close_switch": CLOSE,
        "position_switches": [
            {"entity": P1, "position": 20},
            {"entity": P2, "position": 60},
            {"entity": P3, "position": 100},
        ],
    }
    raw.update(overrides)
    return FallbackConfig.parse(raw)


class TestParse:
    def test_typical_install(self) -> None:
        cfg = build()
        assert cfg.configured is True
        assert cfg.can_stop is False, "4 wires means no stop terminal"
        assert cfg.available_positions == (0, 20, 60, 100)

    def test_unconfigured_is_inert(self) -> None:
        """The default must do nothing -- most installs have no contacts wired."""
        cfg = FallbackConfig.parse(None)
        assert cfg.configured is False
        assert cfg.resolve(50) is None
        assert cfg.available_positions == ()

    def test_close_only(self) -> None:
        cfg = FallbackConfig.parse({"close_switch": CLOSE})
        assert cfg.configured is True
        assert cfg.available_positions == (0,)

    def test_incomplete_stops_are_dropped(self) -> None:
        cfg = FallbackConfig.parse(
            {
                "position_switches": [
                    {"entity": P1, "position": 20},
                    {"entity": P2},  # no position
                    {"position": 60},  # no entity
                    "nonsense",
                ]
            }
        )
        assert cfg.available_positions == (20,)

    def test_stops_are_sorted(self) -> None:
        cfg = FallbackConfig.parse(
            {
                "position_switches": [
                    {"entity": P3, "position": 100},
                    {"entity": P1, "position": 20},
                ]
            }
        )
        assert [s.position for s in cfg.stops] == [20, 100]

    @pytest.mark.parametrize("bad", ["abc", None, ""])
    def test_bad_pulse_duration_falls_back_to_default(self, bad: object) -> None:
        assert FallbackConfig.parse({"pulse_duration": bad}).pulse_duration == 0.5

    def test_negative_pulse_is_clamped(self) -> None:
        assert FallbackConfig.parse({"pulse_duration": -3}).pulse_duration == 0.0

    def test_stop_switch_enables_stop(self) -> None:
        assert build(stop_switch="switch.window_stop").can_stop is True


class TestResolve:
    @pytest.mark.parametrize(
        ("target", "expected_entity", "expected_position"),
        [
            (0, CLOSE, 0),
            (100, P3, 100),
            (60, P2, 60),
            (20, P1, 20),
            (55, P2, 60),
            (35, P1, 20),
            # 5 is nearer to closed than to 20, but it is an *open* request, so
            # it must go to the lowest open stop rather than fire the close relay.
            (5, P1, 20),
            (95, P3, 100),
        ],
    )
    def test_snaps_to_nearest(
        self, target: int, expected_entity: str, expected_position: int
    ) -> None:
        stop = build().resolve(target)
        assert stop == ContactStop(entity_id=expected_entity, position=expected_position)

    def test_tie_prefers_the_lower_position(self) -> None:
        """Exactly between 20 and 60: err toward closed, not further open."""
        stop = build().resolve(40)
        assert stop is not None
        assert stop.position == 20

    def test_zero_prefers_close_over_a_zero_stop(self) -> None:
        cfg = FallbackConfig.parse(
            {"close_switch": CLOSE, "position_switches": [{"entity": P1, "position": 0}]}
        )
        stop = cfg.resolve(0)
        assert stop is not None
        assert stop.entity_id == CLOSE

    def test_open_request_never_fires_the_close_relay(self) -> None:
        """A close-only install cannot open. Firing the close relay for an open
        request would move the window in the opposite direction from the one
        the user asked for -- refusing is the only honest answer."""
        cfg = FallbackConfig.parse({"close_switch": CLOSE})
        assert cfg.resolve(100) is None
        assert cfg.resolve(1) is None
        assert cfg.can_open is False

    def test_open_request_skips_a_zero_position_stop(self) -> None:
        """A stop configured at 0% is a close in disguise; same rule applies."""
        cfg = FallbackConfig.parse({"position_switches": [{"entity": P1, "position": 0}]})
        assert cfg.resolve(50) is None
        assert cfg.can_open is False

    def test_open_only_install_cannot_reach_zero_exactly(self) -> None:
        """Without a close wire, 0 snaps to the lowest open stop."""
        cfg = FallbackConfig.parse({"position_switches": [{"entity": P1, "position": 20}]})
        stop = cfg.resolve(0)
        assert stop is not None
        assert stop.position == 20

    def test_full_install_can_open(self) -> None:
        assert build().can_open is True

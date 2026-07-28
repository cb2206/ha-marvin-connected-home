"""Tests for the auto-venting preference entities.

The risk these guard against is a preference key going out under the wrong
spelling. Reading a house gives `temperatureUpperLimit`; writing needs
`tempUpperLimit`, and the client translates between them — but only if the
entity hands it the *read* spelling. An entity that stored the write spelling
would round-trip fine through the client and then fail to match anything when
read back, leaving a permanently unknown entity.
"""

from __future__ import annotations

import importlib

import pytest

_number = importlib.import_module("marvin_connected_home.number")
_switch = importlib.import_module("marvin_connected_home.switch")
_select = importlib.import_module("marvin_connected_home.select")

LIMITS = _number.LIMITS
PREFERENCE_SWITCHES = _switch.PREFERENCE_SWITCHES

# Exactly the keys observed in `GET /houses/{id}` -> preferences.
READ_KEYS = {
    "temperatureLowerLimit",
    "temperatureUpperLimit",
    "humidityLowerLimit",
    "humidityUpperLimit",
    "temperatureOpenIfEnabled",
    "temperatureCloseIfEnabled",
    "humidityDewPointOpenIfEnabled",
    "humidityDewPointCloseIfEnabled",
    "humidityDewPointToggle",
}


class TestPreferenceKeys:
    def test_every_entity_uses_a_read_spelling(self) -> None:
        used = (
            {d.preference_key for d in LIMITS}
            | {d.preference_key for d in PREFERENCE_SWITCHES}
            | {_select.PREFERENCE_KEY}
        )
        assert used <= READ_KEYS, f"not a captured read key: {sorted(used - READ_KEYS)}"

    @pytest.mark.parametrize(
        "key", ["tempUpperLimit", "tempLowerLimit", "tempOpenIfEnabled", "tempCloseIfEnabled"]
    )
    def test_no_entity_stores_a_write_spelling(self, key: str) -> None:
        """The abbreviated names are what goes on the wire, never what is read
        back. Storing one here would break the read path silently."""
        stored = {d.preference_key for d in LIMITS} | {
            d.preference_key for d in PREFERENCE_SWITCHES
        }
        assert key not in stored

    def test_keys_are_unique(self) -> None:
        keys = [d.preference_key for d in LIMITS] + [
            d.preference_key for d in PREFERENCE_SWITCHES
        ]
        assert len(keys) == len(set(keys))


class TestLimits:
    def test_temperature_limits_are_fahrenheit(self) -> None:
        for description in LIMITS:
            if "temperature" in description.key:
                assert description.native_unit_of_measurement == "°F"

    def test_humidity_limits_are_percentages(self) -> None:
        for description in LIMITS:
            if "humidity" in description.key:
                assert description.native_unit_of_measurement == "%"
                assert (description.native_min_value, description.native_max_value) == (0, 100)


class TestSentinelHandling:
    """Unset limits come back as int min, not null. Rendered raw, an unset
    dew-point limit is a temperature of minus two billion."""

    @pytest.mark.parametrize(
        ("stored", "expected"),
        [(68, 68), (0, 0), (-2147483648, None), (None, None), ("68", None), (True, None)],
    )
    def test_preference_reader(self, stored: object, expected: int | None) -> None:
        house = type("House", (), {"preferences": {"k": stored} if stored is not None else {}})()
        assert _number._preference(house, "k") == expected

    def test_no_house_is_none(self) -> None:
        assert _number._preference(None, "k") is None

    def test_zero_is_kept(self) -> None:
        """A humidity limit of 0 is legitimate and must not read as unknown."""
        house = type("House", (), {"preferences": {"humidityLowerLimit": 0}})()
        assert _number._preference(house, "humidityLowerLimit") == 0


class TestMoistureMetricSelect:
    def test_options_match_captured_api_values(self) -> None:
        assert set(_select.OPTIONS) == {"humidity", "dew_point"}

    def test_unknown_api_value_is_not_forced_into_an_option(self) -> None:
        """If Marvin adds a third mode, reporting None beats asserting one of
        the two we happen to know."""
        assert _select.OPTIONS.get("wet_bulb") is None

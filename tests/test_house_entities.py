"""Tests for entities added after the second capture session.

Two things are pinned here that a reader would otherwise be tempted to
"correct":

* temperatures are declared **Fahrenheit**, not Celsius;
* a zero error count and a `False` condition are values, not absences.

Home Assistant is stubbed in `conftest.py`, so this runs without an install.
"""

from __future__ import annotations

import importlib
from types import SimpleNamespace

import pytest

_binary_sensor = importlib.import_module("marvin_connected_home.binary_sensor")
_sensor = importlib.import_module("marvin_connected_home.sensor")

ASSET_SENSORS = _binary_sensor.ASSET_SENSORS
HOUSE_SENSORS = _sensor.HOUSE_SENSORS


def _house_sensor(key: str):
    return next(d for d in HOUSE_SENSORS if d.key == key)


class TestTemperatureUnit:
    """Marvin's API is Fahrenheit-only: the limits it reports are 54-77, there
    is no unit key anywhere in the API, and setting the Android device to
    Celsius did not change the app's display. Home Assistant converts to the
    user's preferred unit from the declared native one, so declaring Celsius
    here silently scales every reading."""

    @pytest.mark.parametrize(
        "key",
        ["indoor_temperature", "indoor_dew_point", "outdoor_temperature", "outdoor_dew_point"],
    )
    def test_temperatures_are_fahrenheit(self, key: str) -> None:
        assert _house_sensor(key).native_unit_of_measurement == "°F"

    def test_no_celsius_remains(self) -> None:
        units = {d.native_unit_of_measurement for d in HOUSE_SENSORS}
        assert "°C" not in units


class TestAirQualitySensors:
    @pytest.mark.parametrize("key", ["indoor_air_quality", "outdoor_air_quality"])
    def test_sensor_exists_and_reads_its_field(self, key: str) -> None:
        description = _house_sensor(key)
        environment = SimpleNamespace(**{key: 42})
        assert description.value_fn(environment) == 42

    @pytest.mark.parametrize("key", ["indoor_air_quality", "outdoor_air_quality"])
    def test_no_device_class_claimed(self, key: str) -> None:
        """Marvin's index is not a standard AQI. Declaring SensorDeviceClass.AQI
        would assert a scale that has never been verified."""
        assert _house_sensor(key).device_class is None


class TestFaultSensor:
    @pytest.mark.parametrize(
        ("error_count", "expected"),
        [(0, False), (1, True), (5, True), (None, None)],
    )
    def test_error_count_maps_to_problem(self, error_count: int | None, expected: bool | None) -> None:
        description = next(d for d in ASSET_SENSORS if d.key == "fault")
        assert description.value_fn(SimpleNamespace(error_count=error_count)) is expected

    def test_zero_is_not_unknown(self) -> None:
        """`if not error_count` would collapse a real "no faults" into unknown,
        leaving the entity unavailable on every healthy window."""
        description = next(d for d in ASSET_SENSORS if d.key == "fault")
        assert description.value_fn(SimpleNamespace(error_count=0)) is False

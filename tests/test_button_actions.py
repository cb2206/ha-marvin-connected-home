"""Tests for the device-action button table.

`button.py` dispatches through a table of lambdas, one per action, all with the
same signature. A copy-paste slip there wires *Recalibrate* to reboot — or
worse, *Reboot* to recalibrate, which would drive a real window through full
travel on a press that promises not to move it. Nothing else in the stack would
catch that, so the mapping is pinned here.

Home Assistant is stubbed in `conftest.py`, so this runs without an install.
These tests are about which client method each button reaches for, not what it
sends — the request shapes are pinned in the client library's own
`tests/test_device_actions.py`.
"""

from __future__ import annotations

import asyncio
import importlib

import pytest

_button = importlib.import_module("marvin_connected_home.button")
BUTTONS = _button.BUTTONS


class _RecordingClient:
    """Records which client method a button's lambda reached for."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def async_perform_ota(self, device_id: str) -> None:
        self.calls.append(("perform_ota", device_id))

    async def async_reboot_device(self, device_id: str) -> None:
        self.calls.append(("reboot", device_id))

    async def async_recalibrate_device(self, device_id: str) -> None:
        self.calls.append(("recalibrate", device_id))


def _by_key(key: str):
    return next(d for d in BUTTONS if d.key == key)


class TestDispatch:
    @pytest.mark.parametrize(
        ("key", "expected"),
        [
            ("check_firmware", "perform_ota"),
            ("reboot", "reboot"),
            ("recalibrate", "recalibrate"),
        ],
    )
    def test_button_calls_its_own_action(self, key: str, expected: str) -> None:
        client = _RecordingClient()
        # asyncio.run rather than an async test: CI installs bare pytest, with
        # no asyncio plugin, and one coroutine does not justify adding one.
        asyncio.run(_by_key(key).press(client, "eval3-TEST"))
        assert client.calls == [(expected, "eval3-TEST")]

    def test_every_button_is_distinct(self) -> None:
        keys = [d.key for d in BUTTONS]
        assert len(keys) == len(set(keys)) == 3


class TestSafety:
    def test_recalibrate_is_disabled_by_default(self) -> None:
        """The only guard Home Assistant's entity model offers against a press
        that opens a real window."""
        assert _by_key("recalibrate").entity_registry_enabled_default is False

    @pytest.mark.parametrize("key", ["check_firmware", "reboot"])
    def test_harmless_actions_stay_enabled(self, key: str) -> None:
        assert _by_key(key).entity_registry_enabled_default is True

    def test_every_button_has_an_error_message(self) -> None:
        for description in BUTTONS:
            assert description.error and not description.error.endswith(".")

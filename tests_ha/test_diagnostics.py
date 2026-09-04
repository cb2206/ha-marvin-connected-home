"""Diagnostics must never carry the credential or identify the home."""

from __future__ import annotations

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry
from pytest_homeassistant_custom_component.components.diagnostics import (
    get_diagnostics_for_config_entry,
)
from pytest_homeassistant_custom_component.typing import ClientSessionGenerator

from .conftest import HOUSE_ID, STORED_TOKEN, Cloud


async def test_redaction(
    hass: HomeAssistant,
    hass_client: ClientSessionGenerator,
    cloud: Cloud,
    loaded_entry: MockConfigEntry,
) -> None:
    diagnostics = await get_diagnostics_for_config_entry(hass, hass_client, loaded_entry)
    dumped = str(diagnostics)
    assert STORED_TOKEN not in dumped
    assert HOUSE_ID not in dumped
    assert "00:11:22:33:44:55" not in dumped
    assert diagnostics["entry"]["refresh_token"] == "**REDACTED**"
    assert diagnostics["assets"][0]["sash_position"] == 42

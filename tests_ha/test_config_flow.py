"""Config, reauth and options flows, end to end."""

from __future__ import annotations

from typing import Any

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from marvin_connected_home import MarvinAuthError
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.marvin_connected_home.const import (
    CONF_CLOSE_SWITCH,
    CONF_FALLBACK,
    CONF_HOUSE_ID,
    CONF_POSITION_SWITCHES,
    CONF_REFRESH_TOKEN,
    DOMAIN,
)

from .conftest import ASSET_ID, HOUSE_ID, ROTATED_TOKEN, Cloud


def _redirect(result: dict[str, Any], code: str = "abc") -> str:
    """The URL a user would paste back, carrying this flow's own state."""
    url = result["description_placeholders"]["authorize_url"]
    state = url.rsplit("state=", 1)[1]
    return f"https://jwt.ms#code={code}&state={state}"


async def test_user_flow_creates_entry_with_rotated_token(hass: HomeAssistant, cloud: Cloud) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": _redirect(result)}
    )
    # One house: the house step is skipped and the entry created directly.
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "Test House"
    assert result["data"][CONF_HOUSE_ID] == HOUSE_ID
    # Listing houses rotated the token; the entry must hold the *new* one.
    assert result["data"][CONF_REFRESH_TOKEN] == ROTATED_TOKEN
    assert result["result"].unique_id == HOUSE_ID


async def test_user_flow_rejects_foreign_state(hass: HomeAssistant, cloud: Cloud) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "https://jwt.ms#code=abc&state=someone-elses"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "state_mismatch"}


async def test_user_flow_needs_a_code(hass: HomeAssistant, cloud: Cloud) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": "https://jwt.ms"}
    )
    assert result["errors"] == {"base": "no_code"}


async def test_user_flow_stale_code(hass: HomeAssistant, cloud: Cloud) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    redirect = _redirect(result)
    from .conftest import FakeProvider

    original = FakeProvider._exchange

    async def rejected(self: FakeProvider, *args: Any) -> None:
        raise MarvinAuthError("AADB2C90080 expired")

    FakeProvider._exchange = rejected  # type: ignore[method-assign]
    try:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {"redirect_url": redirect}
        )
    finally:
        FakeProvider._exchange = original  # type: ignore[method-assign]
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_code"}


async def test_house_already_configured(
    hass: HomeAssistant, cloud: Cloud, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": _redirect(result)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_token_and_reloads(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    loaded_entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": _redirect(result)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    await hass.async_block_till_done()
    # The exchanged token, then rotated by the house check.
    assert loaded_entry.data[CONF_REFRESH_TOKEN] == ROTATED_TOKEN
    assert loaded_entry.state is config_entries.ConfigEntryState.LOADED


async def test_reauth_with_another_account_is_refused(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    assert cloud.client is not None
    cloud.client.houses = [{"id": "House_other", "name": "Someone else's"}]
    loaded_entry.async_start_reauth(hass)
    await hass.async_block_till_done()
    (flow,) = hass.config_entries.flow.async_progress_by_handler(DOMAIN)
    result = await hass.config_entries.flow.async_configure(flow["flow_id"])
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {"redirect_url": _redirect(result)}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_account_mismatch"


async def test_options_flow_stores_wiring_per_window(
    hass: HomeAssistant, cloud: Cloud, loaded_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(loaded_entry.entry_id)
    assert result["step_id"] == "init"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"asset": ASSET_ID}
    )
    assert result["step_id"] == "window"
    result = await hass.config_entries.options.async_configure(
        result["flow_id"],
        {
            "close_switch": "switch.relay_close",
            "position_1_switch": "switch.relay_open",
            "position_1_percent": 20,
            "position_2_percent": 60,
            "position_3_percent": 100,
            "pulse_duration": 0.5,
            "notify_on_switchover": True,
        },
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    wiring = loaded_entry.options[CONF_FALLBACK][ASSET_ID]
    assert wiring[CONF_CLOSE_SWITCH] == "switch.relay_close"
    assert wiring[CONF_POSITION_SWITCHES] == [{"entity": "switch.relay_open", "position": 20}]


async def test_options_flow_aborts_when_not_loaded(
    hass: HomeAssistant, cloud: Cloud, config_entry: MockConfigEntry
) -> None:
    result = await hass.config_entries.options.async_init(config_entry.entry_id)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "entry_not_loaded"

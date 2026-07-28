"""Config flow.

Marvin's B2C app registers ``aurora://login/verify`` — a custom URI scheme. Home
Assistant cannot receive that redirect, and new redirect URIs cannot be
registered against Marvin's tenant, so HA's usual OAuth2 helper does not apply.

Instead the user signs in in their own browser and pastes back the URL they were
redirected to. PKCE means the pasted code is useless to anyone who intercepts
it: only the verifier held by this flow can redeem it.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import parse_qs, urlparse

import voluptuous as vol
from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlowWithReload,
)
from homeassistant.core import callback
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from marvin_connected_home import (
    B2CTokenProvider,
    MarvinAuthError,
    MarvinClient,
    MarvinConnectionError,
    MarvinError,
    generate_pkce_pair,
)
from marvin_connected_home.const import B2C_REDIRECT_URI

from .const import (
    CONF_CLOSE_SWITCH,
    CONF_CONTACT_SENSOR,
    CONF_FALLBACK,
    CONF_HOUSE_ID,
    CONF_NOTIFY_ON_SWITCHOVER,
    CONF_POSITION,
    CONF_POSITION_SWITCHES,
    CONF_PULSE_DURATION,
    CONF_REFRESH_TOKEN,
    CONF_STOP_SWITCH,
    CONF_SWITCH_ENTITY,
    DEFAULT_NOTIFY_ON_SWITCHOVER,
    DEFAULT_PULSE_DURATION,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

CONF_REDIRECT_URL = "redirect_url"
CONF_ASSET = "asset"

# Field names for the per-window fallback step. Flattened into three
# switch/percentage pairs rather than a dynamic list, because HA config-flow
# forms cannot grow rows and a window has exactly three open stops.
F_CLOSE_SWITCH = "close_switch"
F_STOP_SWITCH = "stop_switch"
F_CONTACT_SENSOR = "contact_sensor"
F_POS1_SWITCH = "position_1_switch"
F_POS1_PCT = "position_1_percent"
F_POS2_SWITCH = "position_2_switch"
F_POS2_PCT = "position_2_percent"
F_POS3_SWITCH = "position_3_switch"
F_POS3_PCT = "position_3_percent"


class MarvinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial setup and re-authentication."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> MarvinOptionsFlow:
        return MarvinOptionsFlow()

    def __init__(self) -> None:
        self._verifier: str | None = None
        self._authorize_url: str | None = None
        self._refresh_token: str | None = None
        self._houses: dict[str, str] = {}

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Show the sign-in URL and collect the redirect."""
        session = async_get_clientsession(self.hass)

        if self._verifier is None:
            verifier, challenge = generate_pkce_pair()
            self._verifier = verifier
            self._authorize_url = B2CTokenProvider(session).build_authorization_url(
                B2C_REDIRECT_URI, challenge, state="hass"
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            code = _extract_code(user_input[CONF_REDIRECT_URL])
            if not code:
                errors["base"] = "no_code"
            else:
                provider = B2CTokenProvider(session)
                try:
                    await provider.async_exchange_code(
                        code, self._verifier, B2C_REDIRECT_URI
                    )
                except MarvinAuthError:
                    # Codes are single-use and short-lived; a stale one is the
                    # most common cause, so send the user back for a fresh one.
                    errors["base"] = "invalid_code"
                except MarvinConnectionError:
                    errors["base"] = "cannot_connect"
                else:
                    if not provider.refresh_token:
                        errors["base"] = "no_refresh_token"
                    else:
                        self._refresh_token = provider.refresh_token
                        return await self.async_step_house()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_REDIRECT_URL): str}),
            errors=errors,
            description_placeholders={"authorize_url": self._authorize_url or ""},
        )

    async def async_step_house(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a house. Accounts may hold more than one."""
        session = async_get_clientsession(self.hass)
        assert self._refresh_token is not None
        provider = B2CTokenProvider(session, refresh_token=self._refresh_token)
        client = MarvinClient(session, provider)

        if not self._houses:
            try:
                for record in await client.async_get_houses():
                    for house in _unwrap(record):
                        house_id = str(house.get("id") or house.get("houseId") or "")
                        if house_id:
                            self._houses[house_id] = str(house.get("name") or house_id)
            except MarvinError as err:
                _LOGGER.debug("Could not list houses: %s", err)
                return self.async_abort(reason="cannot_connect")

            if not self._houses:
                return self.async_abort(reason="no_houses")

        # Nothing to choose between; skip the step rather than show a form
        # with a single option.
        if user_input is None and len(self._houses) == 1:
            user_input = {CONF_HOUSE_ID: next(iter(self._houses))}

        if user_input is not None:
            house_id = user_input[CONF_HOUSE_ID]
            await self.async_set_unique_id(house_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(
                title=self._houses.get(house_id, "Marvin Connected Home"),
                data={
                    CONF_REFRESH_TOKEN: self._refresh_token,
                    CONF_HOUSE_ID: house_id,
                },
            )

        return self.async_show_form(
            step_id="house",
            data_schema=vol.Schema({vol.Required(CONF_HOUSE_ID): vol.In(self._houses)}),
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Refresh tokens rotate and can be revoked; sign in again."""
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        session = async_get_clientsession(self.hass)

        if self._verifier is None:
            verifier, challenge = generate_pkce_pair()
            self._verifier = verifier
            self._authorize_url = B2CTokenProvider(session).build_authorization_url(
                B2C_REDIRECT_URI, challenge, state="hass"
            )

        errors: dict[str, str] = {}
        if user_input is not None:
            code = _extract_code(user_input[CONF_REDIRECT_URL])
            if not code:
                errors["base"] = "no_code"
            else:
                provider = B2CTokenProvider(session)
                try:
                    await provider.async_exchange_code(
                        code, self._verifier, B2C_REDIRECT_URI
                    )
                except MarvinAuthError:
                    errors["base"] = "invalid_code"
                except MarvinConnectionError:
                    errors["base"] = "cannot_connect"
                else:
                    if provider.refresh_token:
                        return self.async_update_reload_and_abort(
                            self._get_reauth_entry(),
                            data_updates={CONF_REFRESH_TOKEN: provider.refresh_token},
                        )
                    errors["base"] = "no_refresh_token"

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_REDIRECT_URL): str}),
            errors=errors,
            description_placeholders={"authorize_url": self._authorize_url or ""},
        )


class MarvinOptionsFlow(OptionsFlowWithReload):
    """Configure the optional dry-contact fallback, per window.

    Entirely optional: most installs have no contacts wired, and leaving this
    empty means nothing ever pulses a relay. Where contacts *are* wired, the
    positions are the user's to declare -- the mapping between Marvin's
    ``hA*Position`` keys and the physical terminals is undocumented, so those
    values only pre-fill the form as a starting point to check against the
    actual wiring.
    """

    def __init__(self) -> None:
        self._asset_id: str | None = None

    @property
    def _coordinator(self) -> Any:
        return self.hass.data[DOMAIN][self.config_entry.entry_id]

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Pick a window to configure."""
        house = self._coordinator.data
        assets = [
            asset
            for asset in (house.assets if house else [])
            if (device := asset.primary) is not None and device.capabilities.sash
        ]
        if not assets:
            return self.async_abort(reason="no_sashes")

        if user_input is not None:
            self._asset_id = user_input[CONF_ASSET]
            return await self.async_step_window()

        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_ASSET): vol.In(
                        {asset.asset_id: asset.name or asset.asset_id for asset in assets}
                    )
                }
            ),
        )

    async def async_step_window(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        assert self._asset_id is not None
        asset = self._coordinator.asset(self._asset_id)
        device = asset.primary if asset else None

        if user_input is not None:
            stops = [
                {CONF_SWITCH_ENTITY: user_input[switch], CONF_POSITION: user_input[position]}
                for switch, position in (
                    (F_POS1_SWITCH, F_POS1_PCT),
                    (F_POS2_SWITCH, F_POS2_PCT),
                    (F_POS3_SWITCH, F_POS3_PCT),
                )
                if user_input.get(switch)
            ]
            entry_options = dict(self.config_entry.options)
            configured = dict(entry_options.get(CONF_FALLBACK) or {})
            configured[self._asset_id] = {
                CONF_CLOSE_SWITCH: user_input.get(F_CLOSE_SWITCH) or None,
                CONF_STOP_SWITCH: user_input.get(F_STOP_SWITCH) or None,
                CONF_POSITION_SWITCHES: stops,
                CONF_CONTACT_SENSOR: user_input.get(F_CONTACT_SENSOR) or None,
                CONF_PULSE_DURATION: user_input[CONF_PULSE_DURATION],
                CONF_NOTIFY_ON_SWITCHOVER: user_input[CONF_NOTIFY_ON_SWITCHOVER],
            }
            entry_options[CONF_FALLBACK] = configured
            return self.async_create_entry(data=entry_options)

        existing = (self.config_entry.options.get(CONF_FALLBACK) or {}).get(
            self._asset_id
        ) or {}
        stops = existing.get(CONF_POSITION_SWITCHES) or []

        def stop_at(index: int, key: str, default: Any) -> Any:
            if index < len(stops) and isinstance(stops[index], dict):
                return stops[index].get(key, default)
            return default

        # Suggested percentages come from the device's own contact configuration.
        contacts = device.contact_positions if device else None
        suggested = [
            getattr(contacts, "position_1", None) or 20,
            getattr(contacts, "position_2", None) or 60,
            getattr(contacts, "position_3", None) or 100,
        ]

        switch_selector = selector.EntitySelector(
            selector.EntitySelectorConfig(domain="switch")
        )
        percent_selector = selector.NumberSelector(
            selector.NumberSelectorConfig(
                min=0, max=100, step=1, mode=selector.NumberSelectorMode.BOX
            )
        )

        schema = vol.Schema(
            {
                vol.Optional(
                    F_CLOSE_SWITCH,
                    description={"suggested_value": existing.get(CONF_CLOSE_SWITCH)},
                ): switch_selector,
                vol.Optional(
                    F_POS1_SWITCH,
                    description={"suggested_value": stop_at(0, CONF_SWITCH_ENTITY, None)},
                ): switch_selector,
                vol.Optional(
                    F_POS1_PCT, default=stop_at(0, CONF_POSITION, suggested[0])
                ): percent_selector,
                vol.Optional(
                    F_POS2_SWITCH,
                    description={"suggested_value": stop_at(1, CONF_SWITCH_ENTITY, None)},
                ): switch_selector,
                vol.Optional(
                    F_POS2_PCT, default=stop_at(1, CONF_POSITION, suggested[1])
                ): percent_selector,
                vol.Optional(
                    F_POS3_SWITCH,
                    description={"suggested_value": stop_at(2, CONF_SWITCH_ENTITY, None)},
                ): switch_selector,
                vol.Optional(
                    F_POS3_PCT, default=stop_at(2, CONF_POSITION, suggested[2])
                ): percent_selector,
                vol.Optional(
                    F_STOP_SWITCH,
                    description={"suggested_value": existing.get(CONF_STOP_SWITCH)},
                ): switch_selector,
                vol.Optional(
                    F_CONTACT_SENSOR,
                    description={"suggested_value": existing.get(CONF_CONTACT_SENSOR)},
                ): selector.EntitySelector(
                    selector.EntitySelectorConfig(domain="binary_sensor")
                ),
                vol.Optional(
                    CONF_PULSE_DURATION,
                    default=existing.get(CONF_PULSE_DURATION, DEFAULT_PULSE_DURATION),
                ): selector.NumberSelector(
                    selector.NumberSelectorConfig(
                        min=0, max=10, step=0.1, mode=selector.NumberSelectorMode.BOX
                    )
                ),
                vol.Optional(
                    CONF_NOTIFY_ON_SWITCHOVER,
                    default=existing.get(
                        CONF_NOTIFY_ON_SWITCHOVER, DEFAULT_NOTIFY_ON_SWITCHOVER
                    ),
                ): bool,
            }
        )

        return self.async_show_form(
            step_id="window",
            data_schema=schema,
            description_placeholders={"window": (asset.name if asset else self._asset_id) or ""},
        )


def _extract_code(value: str) -> str | None:
    """Accept the full redirect URL, a query string, or a bare code."""
    value = (value or "").strip().strip("'\"")
    if not value:
        return None
    if "=" not in value:
        return value
    query = parse_qs(urlparse(value).query or value.lstrip("?"))
    found = query.get("code")
    return found[0] if found else None


def _unwrap(record: Any) -> list[dict[str, Any]]:
    """Read through the API's ``{"data": [...]}`` envelope."""
    if isinstance(record, dict):
        if isinstance(data := record.get("data"), list):
            return [item for item in data if isinstance(item, dict)]
        return [record]
    return []

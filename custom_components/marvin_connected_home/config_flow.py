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
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
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

from .const import CONF_HOUSE_ID, CONF_REFRESH_TOKEN, DOMAIN

_LOGGER = logging.getLogger(__name__)

CONF_REDIRECT_URL = "redirect_url"


class MarvinConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle initial setup and re-authentication."""

    VERSION = 1

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

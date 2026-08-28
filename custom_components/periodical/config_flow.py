"""Config flow for the Periodical integration."""
from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PeriodicalApi, PeriodicalApiError, PeriodicalAuthError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_USER_ID,
    CONF_USER_NAME,
    DEFAULT_BASE_URL,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
    }
)

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


class PeriodicalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Periodical config flow."""

    VERSION = 1

    def __init__(self) -> None:
        self._reauth_entry: ConfigEntry | None = None

    async def _async_validate(self, api_key: str, base_url: str) -> tuple[dict | None, str | None]:
        """Return (/me payload, error key)."""
        session = async_get_clientsession(self.hass)
        api = PeriodicalApi(base_url=base_url, api_key=api_key, session=session)
        try:
            return await api.get_me(), None
        except PeriodicalAuthError:
            return None, "invalid_auth"
        except PeriodicalApiError:
            return None, "cannot_connect"
        except Exception:  # noqa: BLE001
            _LOGGER.exception("Unexpected error validating Periodical credentials")
            return None, "unknown"

    @staticmethod
    def _extract_user(me: Mapping[str, Any]) -> tuple[int | None, str]:
        """Pull the numeric id and a display name out of a /me payload."""
        try:
            user_id = int(me["id"])
        except (KeyError, TypeError, ValueError):
            user_id = None
        name = me.get("name") or me.get("username") or f"User {user_id}"
        return user_id, str(name)

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).strip().rstrip("/")

            me, error = await self._async_validate(api_key, base_url)
            if error:
                errors["base"] = error
            else:
                user_id, user_name = self._extract_user(me or {})
                if not user_id:
                    _LOGGER.error("/me response had no usable integer user id: %s", me)
                    errors["base"] = "no_user_id"
                else:
                    await self.async_set_unique_id(f"{DOMAIN}_{user_id}")
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=user_name,
                        data={
                            CONF_API_KEY: api_key,
                            CONF_BASE_URL: base_url,
                            CONF_USER_ID: user_id,
                            CONF_USER_NAME: user_name,
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Triggered when the coordinator reports the API key is no longer valid."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        entry = self._reauth_entry

        if entry is None:
            return self.async_abort(reason="reauth_unsuccessful")

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)

            me, error = await self._async_validate(api_key, base_url)
            if error:
                errors["base"] = error
            else:
                user_id, user_name = self._extract_user(me or {})
                if user_id != entry.data[CONF_USER_ID]:
                    # A different account's key would silently repoint every
                    # entity at somebody else's schedule.
                    errors["base"] = "wrong_account"
                else:
                    return self.async_update_reload_and_abort(
                        entry,
                        data={**entry.data, CONF_API_KEY: api_key, CONF_USER_NAME: user_name},
                        reason="reauth_successful",
                    )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=STEP_REAUTH_SCHEMA,
            description_placeholders={"account": entry.data.get(CONF_USER_NAME, "")},
            errors=errors,
        )

"""Config flow for Periodical integration."""
from __future__ import annotations

import logging
from typing import Any

import aiohttp
import voluptuous as vol

from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PeriodicalApi, PeriodicalApiError, PeriodicalAuthError
from .const import CONF_API_KEY, CONF_BASE_URL, CONF_USER_ID, CONF_USER_NAME, DEFAULT_BASE_URL, DOMAIN

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str,
    }
)


class PeriodicalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Periodical config flow."""

    VERSION = 1

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")

            session = async_get_clientsession(self.hass)
            api = PeriodicalApi(base_url=base_url, api_key=api_key, session=session)

            try:
                me = await api.get_me()
            except PeriodicalAuthError:
                errors["base"] = "invalid_auth"
            except PeriodicalApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Unexpected error during config flow")
                errors["base"] = "unknown"
            else:
                # Extract user id and name from the /me response.
                # The API doesn't publish its schema so we probe common field names.
                user_id = (
                    me.get("id")
                    or me.get("user_id")
                    or me.get("userId")
                )
                user_name = (
                    me.get("name")
                    or me.get("full_name")
                    or me.get("username")
                    or me.get("email")
                    or f"User {user_id}"
                )

                if not user_id:
                    _LOGGER.error("/me response did not contain a user id: %s", me)
                    errors["base"] = "no_user_id"
                else:
                    await self.async_set_unique_id(f"{DOMAIN}_{base_url}_{user_id}")
                    self._abort_if_unique_id_configured()

                    return self.async_create_entry(
                        title=str(user_name),
                        data={
                            CONF_API_KEY: api_key,
                            CONF_BASE_URL: base_url,
                            CONF_USER_ID: int(user_id),
                            CONF_USER_NAME: str(user_name),
                        },
                    )

        return self.async_show_form(
            step_id="user",
            data_schema=STEP_USER_SCHEMA,
            errors=errors,
        )
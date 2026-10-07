"""Config flow for the Periodical integration."""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import Any
from urllib.parse import urlsplit

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PeriodicalApi, PeriodicalApiError, PeriodicalAuthError, PeriodicalForbiddenError
from .const import (
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_USER_ID,
    CONF_USER_NAME,
    API_PATH,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)

STEP_USER_SCHEMA = vol.Schema(
    {
        vol.Required(CONF_API_KEY): str,
        vol.Required(CONF_BASE_URL): str,
    }
)


def normalize_base_url(raw: str) -> str | None:
    """Clean up a user-entered server address, or None if it is not usable.

    A bare host such as `https://periodical.example.com` gets the API path
    appended, since that is how Periodical serves its API.  A path the user
    typed is kept as is, for servers mounted somewhere else.
    """
    url = raw.strip().rstrip("/")
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        return None
    if not parts.path:
        url += API_PATH
    return url

STEP_REAUTH_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str})


def _as_user_id(value: Any) -> int | None:
    """Coerce the /me id field to an int, or None if it is missing or malformed."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class PeriodicalConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle the Periodical config flow."""

    VERSION = 1

    def __init__(self) -> None:
        """Track the entry being reauthenticated, if any."""
        self._reauth_entry: ConfigEntry | None = None

    async def _async_identify(
        self, api_key: str, base_url: str
    ) -> tuple[int | None, str, str | None]:
        """Validate a key and identify its owner.

        Returns (user_id, display_name, error_key); user_id is None on failure.
        """
        api = PeriodicalApi(
            base_url=base_url,
            api_key=api_key,
            session=async_get_clientsession(self.hass),
        )
        try:
            me = await api.get_me()
        except (PeriodicalAuthError, PeriodicalForbiddenError):
            return None, "", "invalid_auth"
        except PeriodicalApiError:
            return None, "", "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error validating Periodical credentials")
            return None, "", "unknown"

        user_id = _as_user_id(me.get("id"))
        if user_id is None:
            _LOGGER.error("/me response had no usable integer user id: %s", me)
            return None, "", "no_user_id"

        name = me.get("name") or me.get("username") or f"User {user_id}"
        return user_id, str(name), None

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Collect an API key and create the entry."""
        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = normalize_base_url(user_input[CONF_BASE_URL])

            user_id, user_name, error = (
                (None, "", "invalid_url")
                if base_url is None
                else await self._async_identify(api_key, base_url)
            )
            if error is not None:
                errors["base"] = error
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
            # Keep what was typed when the form comes back with an error.
            data_schema=self.add_suggested_values_to_schema(STEP_USER_SCHEMA, user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: Mapping[str, Any]) -> ConfigFlowResult:
        """Entered when the coordinator reports the API key is no longer valid."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Swap in a replacement API key for the same account."""
        entry = self._reauth_entry
        if entry is None:
            return self.async_abort(reason="reauth_unsuccessful")

        errors: dict[str, str] = {}

        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = entry.data[CONF_BASE_URL]

            user_id, user_name, error = await self._async_identify(api_key, base_url)
            if error is not None:
                errors["base"] = error
            elif user_id != entry.data[CONF_USER_ID]:
                # A different account's key would silently repoint every entity
                # at somebody else's schedule.
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

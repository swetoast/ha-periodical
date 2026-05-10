"""Config flow for Periodical integration."""
from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol
from homeassistant import config_entries
from homeassistant.config_entries import ConfigFlowResult
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PeriodicalApi, PeriodicalApiError, PeriodicalAuthError
from .const import (
    ALL_ENDPOINT_KEYS,
    CONF_API_KEY,
    CONF_BASE_URL,
    CONF_ENABLED_ENDPOINTS,
    CONF_MAX_CONCURRENT_REQUESTS,
    CONF_REQUEST_TIMEOUT_SECONDS,
    CONF_RETRY_ATTEMPTS,
    CONF_UPDATE_INTERVAL_MINUTES,
    CONF_USER_ID,
    CONF_USER_NAME,
    DEFAULT_BASE_URL,
    DEFAULT_MAX_CONCURRENT_REQUESTS,
    DEFAULT_REQUEST_TIMEOUT_SECONDS,
    DEFAULT_RETRY_ATTEMPTS,
    DEFAULT_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
STEP_USER_SCHEMA = vol.Schema({vol.Required(CONF_API_KEY): str, vol.Optional(CONF_BASE_URL, default=DEFAULT_BASE_URL): str})


def _options_schema(options: dict[str, Any]) -> vol.Schema:
    enabled = set(options.get(CONF_ENABLED_ENDPOINTS, ALL_ENDPOINT_KEYS))
    schema: dict[Any, Any] = {
        vol.Optional(CONF_UPDATE_INTERVAL_MINUTES, default=options.get(CONF_UPDATE_INTERVAL_MINUTES, DEFAULT_UPDATE_INTERVAL_MINUTES)): vol.All(vol.Coerce(int), vol.Range(min=5, max=1440)),
        vol.Optional(CONF_REQUEST_TIMEOUT_SECONDS, default=options.get(CONF_REQUEST_TIMEOUT_SECONDS, DEFAULT_REQUEST_TIMEOUT_SECONDS)): vol.All(vol.Coerce(int), vol.Range(min=5, max=120)),
        vol.Optional(CONF_RETRY_ATTEMPTS, default=options.get(CONF_RETRY_ATTEMPTS, DEFAULT_RETRY_ATTEMPTS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
        vol.Optional(CONF_MAX_CONCURRENT_REQUESTS, default=options.get(CONF_MAX_CONCURRENT_REQUESTS, DEFAULT_MAX_CONCURRENT_REQUESTS)): vol.All(vol.Coerce(int), vol.Range(min=1, max=10)),
    }
    for key in ALL_ENDPOINT_KEYS:
        schema[vol.Optional(f"endpoint_{key}", default=key in enabled)] = bool
    return vol.Schema(schema)


class PeriodicalConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: config_entries.ConfigEntry) -> config_entries.OptionsFlow:
        return PeriodicalOptionsFlow(config_entry)

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            return await self._validate_and_create(user_input, errors)
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> ConfigFlowResult:
        self._reauth_entry = self.hass.config_entries.async_get_entry(self.context["entry_id"])
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors: dict[str, str] = {}
        if user_input is not None:
            api_key = user_input[CONF_API_KEY].strip()
            base_url = self._reauth_entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
            api = PeriodicalApi(base_url=base_url, api_key=api_key, session=async_get_clientsession(self.hass))
            try:
                await api.get_me()
            except PeriodicalAuthError:
                errors["base"] = "invalid_auth"
            except PeriodicalApiError:
                errors["base"] = "cannot_connect"
            else:
                self.hass.config_entries.async_update_entry(self._reauth_entry, data={**self._reauth_entry.data, CONF_API_KEY: api_key})
                await self.hass.config_entries.async_reload(self._reauth_entry.entry_id)
                return self.async_abort(reason="reauth_successful")
        return self.async_show_form(step_id="reauth_confirm", data_schema=vol.Schema({vol.Required(CONF_API_KEY): str}), errors=errors)

    async def _validate_and_create(self, user_input: dict[str, Any], errors: dict[str, str]) -> ConfigFlowResult:
        api_key = user_input[CONF_API_KEY].strip()
        base_url = user_input.get(CONF_BASE_URL, DEFAULT_BASE_URL).rstrip("/")
        api = PeriodicalApi(base_url=base_url, api_key=api_key, session=async_get_clientsession(self.hass))
        try:
            me = await api.get_me()
        except PeriodicalAuthError:
            errors["base"] = "invalid_auth"
        except PeriodicalApiError:
            errors["base"] = "cannot_connect"
        except Exception:
            _LOGGER.exception("Unexpected error during config flow")
            errors["base"] = "unknown"
        else:
            user_id = me.get("id") or me.get("user_id") or me.get("userId")
            user_name = me.get("name") or me.get("full_name") or me.get("username") or me.get("email") or f"User {user_id}"
            if not user_id:
                errors["base"] = "no_user_id"
            else:
                await self.async_set_unique_id(f"{DOMAIN}_{base_url}_{user_id}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=str(user_name),
                    data={CONF_API_KEY: api_key, CONF_BASE_URL: base_url, CONF_USER_ID: int(user_id), CONF_USER_NAME: str(user_name)},
                    options={CONF_ENABLED_ENDPOINTS: list(ALL_ENDPOINT_KEYS), CONF_UPDATE_INTERVAL_MINUTES: DEFAULT_UPDATE_INTERVAL_MINUTES, CONF_REQUEST_TIMEOUT_SECONDS: DEFAULT_REQUEST_TIMEOUT_SECONDS, CONF_RETRY_ATTEMPTS: DEFAULT_RETRY_ATTEMPTS, CONF_MAX_CONCURRENT_REQUESTS: DEFAULT_MAX_CONCURRENT_REQUESTS},
                )
        return self.async_show_form(step_id="user", data_schema=STEP_USER_SCHEMA, errors=errors)


class PeriodicalOptionsFlow(config_entries.OptionsFlow):
    def __init__(self, config_entry: config_entries.ConfigEntry) -> None:
        self.config_entry = config_entry

    async def async_step_init(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        if user_input is not None:
            enabled = [key for key in ALL_ENDPOINT_KEYS if user_input.pop(f"endpoint_{key}", False)]
            return self.async_create_entry(title="", data={**user_input, CONF_ENABLED_ENDPOINTS: enabled})
        return self.async_show_form(step_id="init", data_schema=_options_schema(dict(self.config_entry.options)))

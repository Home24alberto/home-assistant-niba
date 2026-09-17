"""Config flow for Niba Energía."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NibaApiClient, NibaApiError, NibaAuthenticationError
from .const import DOMAIN


class NibaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Niba Energía."""

    VERSION = 1

    async def _async_validate_credentials(self, email: str, password: str) -> None:
        """Validate Niba credentials without retaining the session token."""
        api = NibaApiClient(
            async_get_clientsession(self.hass),
            email,
            password,
        )
        await api.async_login()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Handle the initial configuration step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            email = user_input[CONF_EMAIL].strip()
            password = user_input[CONF_PASSWORD]

            await self.async_set_unique_id(email.lower())
            self._abort_if_unique_id_configured()

            try:
                await self._async_validate_credentials(email, password)
            except NibaAuthenticationError:
                errors["base"] = "invalid_auth"
            except NibaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                return self.async_create_entry(
                    title="Niba Energía",
                    data={
                        CONF_EMAIL: email,
                        CONF_PASSWORD: password,
                    },
                )

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    vol.Required(CONF_EMAIL): str,
                    vol.Required(CONF_PASSWORD): str,
                }
            ),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> config_entries.ConfigFlowResult:
        """Start reauthentication for an existing entry."""
        self._reauth_entry = self.hass.config_entries.async_get_entry(
            self.context["entry_id"]
        )
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> config_entries.ConfigFlowResult:
        """Confirm new credentials and reload the existing entry."""
        errors: dict[str, str] = {}

        if self._reauth_entry is None:
            return self.async_abort(reason="reauth_entry_missing")

        email = self._reauth_entry.data[CONF_EMAIL]

        if user_input is not None:
            password = user_input[CONF_PASSWORD]

            try:
                await self._async_validate_credentials(email, password)
            except NibaAuthenticationError:
                errors["base"] = "invalid_auth"
            except NibaApiError:
                errors["base"] = "cannot_connect"
            except Exception:  # noqa: BLE001
                errors["base"] = "unknown"
            else:
                return self.async_update_reload_and_abort(
                    self._reauth_entry,
                    data_updates={CONF_PASSWORD: password},
                )

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): str}),
            description_placeholders={"email": email},
            errors=errors,
        )

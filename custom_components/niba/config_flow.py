"""Config flow for Niba Energía."""

from __future__ import annotations

from datetime import date
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.core import callback
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import NibaApiClient, NibaApiError, NibaAuthenticationError
from .const import (
    CONF_ELECTRICITY_TAX,
    CONF_ENERGY_LLANO,
    CONF_ENERGY_PUNTA,
    CONF_ENERGY_VALLE,
    CONF_INVOICE_ESTIMATE,
    CONF_POWER_P1_DAY,
    CONF_POWER_P1_KW,
    CONF_POWER_P2_DAY,
    CONF_POWER_P2_KW,
    CONF_RENTAL_DAY,
    CONF_SOCIAL_DAY,
    CONF_SURPLUS_KWH,
    CONF_VALLEY_DATES,
    CONF_VAT,
    DEFAULT_TARIFF_OPTIONS,
    DOMAIN,
)


class NibaConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Niba Energía."""

    VERSION = 1

    @staticmethod
    @callback
    def async_get_options_flow(config_entry):
        """Return the tariff options flow."""
        return NibaOptionsFlow()

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


class NibaOptionsFlow(config_entries.OptionsFlow):
    """Configure an optional invoice forecast with per-user contract data."""

    def __init__(self) -> None:
        super().__init__()
        self._tariff_input: dict[str, Any] = {}

    def _value(self, key: str):
        return self.config_entry.options.get(key, DEFAULT_TARIFF_OPTIONS[key])

    async def async_step_init(self, user_input=None):
        """Collect energy and power prices."""
        errors = {}
        if user_input is not None:
            if user_input[CONF_INVOICE_ESTIMATE]:
                positive = (
                    CONF_ENERGY_PUNTA,
                    CONF_ENERGY_LLANO,
                    CONF_ENERGY_VALLE,
                    CONF_POWER_P1_KW,
                    CONF_POWER_P2_KW,
                    CONF_POWER_P1_DAY,
                    CONF_POWER_P2_DAY,
                )
                if any(float(user_input[key]) <= 0 for key in positive):
                    errors["base"] = "invalid_tariff"
            if not errors:
                self._tariff_input = user_input
                return await self.async_step_charges()

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_INVOICE_ESTIMATE,
                    default=self._value(CONF_INVOICE_ESTIMATE),
                ): bool,
                vol.Required(
                    CONF_ENERGY_PUNTA, default=self._value(CONF_ENERGY_PUNTA)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_ENERGY_LLANO, default=self._value(CONF_ENERGY_LLANO)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_ENERGY_VALLE, default=self._value(CONF_ENERGY_VALLE)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_POWER_P1_KW, default=self._value(CONF_POWER_P1_KW)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_POWER_P2_KW, default=self._value(CONF_POWER_P2_KW)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_POWER_P1_DAY, default=self._value(CONF_POWER_P1_DAY)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_POWER_P2_DAY, default=self._value(CONF_POWER_P2_DAY)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_SURPLUS_KWH, default=self._value(CONF_SURPLUS_KWH)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
            }
        )
        return self.async_show_form(
            step_id="init", data_schema=schema, errors=errors
        )

    async def async_step_charges(self, user_input=None):
        """Collect taxes, regulated charges and national holidays."""
        errors = {}
        if user_input is not None:
            holidays = [
                item.strip()
                for item in user_input[CONF_VALLEY_DATES].split(",")
                if item.strip()
            ]
            try:
                for item in holidays:
                    date.fromisoformat(item)
            except ValueError:
                errors[CONF_VALLEY_DATES] = "invalid_date_list"
            if not errors:
                options = {
                    **self.config_entry.options,
                    **self._tariff_input,
                    **user_input,
                }
                options[CONF_VALLEY_DATES] = ",".join(holidays)
                return self.async_create_entry(title="", data=options)

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_RENTAL_DAY, default=self._value(CONF_RENTAL_DAY)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_SOCIAL_DAY, default=self._value(CONF_SOCIAL_DAY)
                ): vol.All(vol.Coerce(float), vol.Range(min=0)),
                vol.Required(
                    CONF_ELECTRICITY_TAX,
                    default=self._value(CONF_ELECTRICITY_TAX),
                ): vol.All(vol.Coerce(float), vol.Range(min=0, max=100)),
                vol.Required(CONF_VAT, default=self._value(CONF_VAT)): vol.All(
                    vol.Coerce(float), vol.Range(min=0, max=100)
                ),
                vol.Optional(
                    CONF_VALLEY_DATES, default=self._value(CONF_VALLEY_DATES)
                ): str,
            }
        )
        return self.async_show_form(
            step_id="charges", data_schema=schema, errors=errors
        )


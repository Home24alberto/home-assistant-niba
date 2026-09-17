from __future__ import annotations

import logging
from typing import Any

import aiohttp

from .const import API_BASE

_LOGGER = logging.getLogger(__name__)


class NibaApiError(Exception):
    """Base Niba API error."""


class NibaAuthenticationError(NibaApiError):
    """Authentication error."""


class NibaApiClient:
    """Client for the Niba API."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None

    async def async_login(self) -> str:
        """Authenticate and return token."""

        url = f"{API_BASE}/api/users/login"

        try:
            async with self._session.post(
                url,
                json={
                    "email": self._email,
                    "password": self._password,
                },
            ) as response:
                if response.status in (401, 403):
                    raise NibaAuthenticationError(
                        "Las credenciales de Niba no son válidas"
                    )

                if response.status != 200:
                    raise NibaApiError(
                        f"El inicio de sesión devolvió HTTP {response.status}"
                    )

                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError) as err:
                    raise NibaApiError(
                        "Respuesta de login Niba no válida"
                    ) from err

        except aiohttp.ClientError as err:
            raise NibaApiError("No se pudo conectar con Niba") from err

        token = data.get("token")

        if not token:
            raise NibaAuthenticationError(
                "Niba no devolvió token"
            )

        self._token = token
        return token

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> Any:
        """Perform authenticated API request."""

        if not self._token:
            await self.async_login()

        url = f"{API_BASE}{path}"

        for attempt in range(2):
            headers = {
                "Authorization": f"token {self._token}",
                "Accept": "application/json",
            }

            try:
                async with self._session.request(
                    method,
                    url,
                    headers=headers,
                    params=params,
                ) as response:

                    if response.status in (401, 403) and attempt == 0:
                        _LOGGER.debug(
                            "Token Niba caducado. Renovando."
                        )
                        await self.async_login()
                        continue

                    if response.status in (401, 403):
                        raise NibaAuthenticationError(
                            "La sesión de Niba ha caducado"
                        )

                    if response.status != 200:
                        if optional:
                            _LOGGER.debug(
                                "Endpoint opcional Niba devolvió HTTP %s",
                                response.status,
                            )
                            return None

                        raise NibaApiError(
                            f"La API de Niba devolvió HTTP {response.status}"
                        )

                    try:
                        return await response.json()
                    except Exception as err:
                        if optional:
                            _LOGGER.debug(
                                "JSON inválido en un endpoint opcional de Niba",
                            )
                            return None

                        raise NibaApiError(
                            "La API de Niba devolvió una respuesta no válida"
                        ) from err

            except aiohttp.ClientError as err:
                if optional:
                    _LOGGER.debug(
                        "Error de red en endpoint opcional %s: %s",
                        path,
                        err,
                    )
                    return None

                raise NibaApiError("Error de conexión con Niba") from err

        if optional:
            return None

        raise NibaAuthenticationError(
            "No se pudo renovar la sesión Niba"
        )

    async def get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> Any:
        return await self.async_request(
            "GET",
            path,
            params=params,
            optional=optional,
        )

    async def get_user(self):
        return await self.get("/api/users/me")

    async def get_contracts(self):
        return await self.get("/api/contracts")

    async def get_contracts_v2(self):
        return await self.get(
            "/api/v2/contracts",
            optional=True,
        )

    async def get_cups_info(self, cups: str):
        return await self.get(
            f"/api/cups/{cups}",
            optional=True,
        )

    async def get_consumption_period(self, cups: str):
        return await self.get(
            f"/api/cups/{cups}/consumption-period",
            optional=True,
        )

    async def get_consumption_daily(
        self,
        cups: str,
        date_start: str,
        date_end: str,
    ):
        return await self.get(
            f"/api/cups/{cups}/consumption-daily",
            params={
                "date_start": date_start,
                "date_end": date_end,
            },
            optional=True,
        )

    async def get_consumption_monthly(
        self,
        cups: str,
        date_start: str,
        date_end: str,
    ):
        return await self.get(
            f"/api/cups/{cups}/consumption-monthly",
            params={
                "date_start": date_start,
                "date_end": date_end,
            },
            optional=True,
        )

    async def get_consumption_powers(
        self,
        cups: str,
        date_start: str,
        date_end: str,
    ):
        return await self.get(
            f"/api/cups/{cups}/consumption-powers",
            params={
                "date_start": date_start,
                "date_end": date_end,
            },
            optional=True,
        )

    async def get_appliances_consumption(
        self,
        cups: str,
        date_start: str,
        date_end: str,
    ):
        return await self.get(
            f"/api/cups/{cups}/appliances-consumption",
            params={
                "date_start": date_start,
                "date_end": date_end,
            },
            optional=True,
        )

    async def get_bills(self, cups: str):
        return await self.get(
            f"/api/cups/{cups}/bills",
            optional=True,
        )

    async def get_balances(self):
        return await self.get("/api/balances")

    async def get_movements(self):
        return await self.get(
            "/api/movements",
            optional=True,
        )

    async def get_products(self):
        return await self.get(
            "/api/products",
            optional=True,
        )

    async def get_spending_rules(self):
        return await self.get(
            "/api/spending-rules",
            optional=True,
        )

    async def get_payment_cards(self):
        return await self.get(
            "/api/payment-cards",
            optional=True,
        )

    async def get_cases(self):
        return await self.get(
            "/api/cases",
            optional=True,
        )

    async def get_sva_assistances(self):
        return await self.get(
            "/api/sva/assistances",
            optional=True,
        )

    async def get_invitations(self):
        return await self.get(
            "/api/invitations",
            optional=True,
        )

    async def get_document(self, document_id: str):
        return await self.get(
            f"/api/documents/{document_id}",
            optional=True,
        )

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
    """Client for the Niba API.

    Logs and public error messages must not contain credentials,
    tokens, customer identifiers, request URLs or response bodies.
    """

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

        # Only controlled endpoint labels and error categories.
        # Never store the actual request path or response body here.
        self.last_optional_error: dict[str, Any] | None = None

    @staticmethod
    def _endpoint_name(path: str) -> str:
        """Return a controlled, non-sensitive endpoint label."""
        parts = path.strip("/").split("/")

        known_endpoints = {
            "me",
            "contracts",
            "products",
            "bills",
            "balances",
            "movements",
            "consumption-period",
            "consumption-daily",
            "consumption-monthly",
            "consumption-powers",
            "appliances-consumption",
            "spending-rules",
            "payment-cards",
            "cases",
            "invitations",
        }

        endpoint = parts[-1] if parts else ""

        if endpoint in known_endpoints:
            return endpoint

        if "cups" in parts:
            return "cups-info"

        if "documents" in parts:
            return "document"

        if (
            len(parts) >= 2
            and parts[-2:] == ["sva", "assistances"]
        ):
            return "sva-assistances"

        return "api"

    async def async_login(self) -> str:
        """Authenticate without exposing private response details."""
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
                    self._token = None
                    raise NibaAuthenticationError(
                        "Las credenciales de Niba no son válidas"
                    )

                if response.status != 200:
                    raise NibaApiError(
                        "El inicio de sesión devolvió "
                        f"HTTP {response.status}"
                    )

                try:
                    data = await response.json()
                except (aiohttp.ContentTypeError, ValueError):
                    # The original exception may include the request URL.
                    raise NibaApiError(
                        "Respuesta de login Niba no válida"
                    ) from None

        except aiohttp.ClientError:
            raise NibaApiError(
                "No se pudo conectar con Niba"
            ) from None

        except TimeoutError:
            raise NibaApiError(
                "Tiempo de espera agotado al iniciar sesión en Niba"
            ) from None

        token = data.get("token") if isinstance(data, dict) else None

        if not isinstance(token, str) or not token.strip():
            self._token = None
            raise NibaAuthenticationError(
                "Niba no devolvió token"
            )

        self._token = token.strip()
        return self._token

    async def async_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        optional: bool = False,
    ) -> Any:
        """Perform an authenticated request with safe error messages."""
        endpoint = self._endpoint_name(path)

        if optional:
            self.last_optional_error = None

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
                        self._token = None
                        _LOGGER.debug(
                            "Renovando la sesión de Niba"
                        )
                        await self.async_login()
                        continue

                    if response.status in (401, 403):
                        self._token = None
                        raise NibaAuthenticationError(
                            "La sesión de Niba ha caducado"
                        )

                    if response.status != 200:
                        if optional:
                            self.last_optional_error = {
                                "endpoint": endpoint,
                                "tipo": "http",
                                "http_status": response.status,
                            }

                            _LOGGER.debug(
                                "Endpoint opcional Niba '%s': HTTP %s",
                                endpoint,
                                response.status,
                            )
                            return None

                        raise NibaApiError(
                            "La API de Niba devolvió "
                            f"HTTP {response.status}"
                        )

                    try:
                        data = await response.json()

                    except (aiohttp.ContentTypeError, ValueError):
                        if optional:
                            self.last_optional_error = {
                                "endpoint": endpoint,
                                "tipo": "json_invalido",
                            }

                            _LOGGER.debug(
                                "Endpoint opcional Niba '%s': "
                                "JSON no válido",
                                endpoint,
                            )
                            return None

                        raise NibaApiError(
                            "La API de Niba devolvió "
                            "una respuesta no válida"
                        ) from None

                    if optional:
                        self.last_optional_error = None

                    return data

            except aiohttp.ClientError:
                if optional:
                    self.last_optional_error = {
                        "endpoint": endpoint,
                        "tipo": "conexion",
                    }

                    _LOGGER.debug(
                        "Error de conexión en endpoint opcional "
                        "Niba '%s'",
                        endpoint,
                    )
                    return None

                raise NibaApiError(
                    "Error de conexión con Niba"
                ) from None

            except TimeoutError:
                if optional:
                    self.last_optional_error = {
                        "endpoint": endpoint,
                        "tipo": "timeout",
                    }

                    _LOGGER.debug(
                        "Timeout en endpoint opcional Niba '%s'",
                        endpoint,
                    )
                    return None

                raise NibaApiError(
                    "Tiempo de espera agotado al conectar con Niba"
                ) from None

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

    # -----------------------------------------------------
    # CONTRATOS Y DATOS ENERGÉTICOS
    # -----------------------------------------------------

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

    async def get_bills(self, cups: str):
        return await self.get(
            f"/api/cups/{cups}/bills",
            optional=True,
        )

    async def get_balances(self):
        return await self.get("/api/balances")

    # -----------------------------------------------------
    # MÉTODOS CONSERVADOS POR COMPATIBILIDAD
    #
    # El coordinador corregido no utiliza estos métodos.
    # Su definición no realiza ninguna petición por sí sola.
    # -----------------------------------------------------

    async def get_user(self):
        return await self.get("/api/users/me")

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

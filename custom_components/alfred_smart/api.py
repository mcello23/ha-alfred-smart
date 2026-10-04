"""Async client for the (unofficial) Alfred Smart cloud API."""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import aiohttp

from .const import API_BASE, APP_ORIGIN, INTERACT_TIMEOUT, READ_TIMEOUT
from .models import (
    AccessDevice,
    Asset,
    CommonArea,
    GatewayInfo,
    assets_from_devices,
    extract_token,
    jwt_expiry,
    parse_assets,
    parse_common_areas,
    parse_devices,
    parse_gateway,
)

_LOGGER = logging.getLogger(__name__)

_HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Version": "1",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": APP_ORIGIN,
    "Referer": f"{APP_ORIGIN}/",
    "X-App-Web": "true",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}

_DEVICE_LIST_PARAMS = {
    "page[number]": "1",
    "page[size]": "1000",
    "include_shared": "1",
    "show_visible": "1",
}

# Renew a bit before the JWT really expires.
_TOKEN_MARGIN = timedelta(minutes=10)


class AlfredSmartError(Exception):
    """Base error."""


class AlfredConnectionError(AlfredSmartError):
    """Network failure or no answer in time."""


class AlfredTimeoutError(AlfredConnectionError):
    """No answer in time. For an opening, the command may still have run."""


class AlfredAuthError(AlfredSmartError):
    """Wrong credentials, or the session was rejected after a fresh login."""


class AlfredRequestError(AlfredSmartError):
    """The API answered with an error status."""

    def __init__(self, status: int, body: Any = None) -> None:
        super().__init__(f"HTTP {status}")
        self.status = status
        self.body = body


class AlfredForbiddenError(AlfredRequestError):
    """403: the account may see the device but not use it."""


class AlfredNotFoundError(AlfredRequestError):
    """404: endpoint or object does not exist."""


class AlfredGatewayUnreachableError(AlfredRequestError):
    """502/503/504: Alfred's server could not reach the physical gateway.

    `/devices/interact` is synchronous and only answers after the gateway
    acknowledges, so a 502 there means the gateway is offline or stuck. There
    is nothing to fix on the Home Assistant side.
    """


class AlfredSmartClient:
    """Talks to services.alfredsmartdata.com as the web app does."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        *,
        base_url: str = API_BASE,
    ) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._base_url = base_url.rstrip("/")
        self._token: str | None = None
        self._token_expiry: datetime | None = None
        self._login_lock = asyncio.Lock()

    @property
    def token_expiry(self) -> datetime | None:
        """When the current session ends, if the JWT says so."""
        return self._token_expiry

    async def login(self) -> None:
        """Open a new session. Raises AlfredAuthError on bad credentials."""
        payload = {
            "email": self._email,
            "password": self._password,
            "user_device": {
                "device_id": "",
                "device_model": "",
                "vendor": "web",
                "vendor_notification_id": "",
            },
        }
        status, body = await self._raw_request(
            "POST", "/users/login", json=payload, timeout=READ_TIMEOUT
        )
        if status in (400, 401, 403, 404, 422):
            raise AlfredAuthError(f"Login rejected (HTTP {status})")
        if status >= 400:
            raise _error_for(status, body)
        token = extract_token(body)
        if not token:
            raise AlfredAuthError("Login answered without a session token")
        self._token = token
        self._token_expiry = jwt_expiry(token)
        _LOGGER.debug("Logged in, token valid until %s", self._token_expiry)

    async def _ensure_token(self, stale_token: str | None = None) -> None:
        async with self._login_lock:
            # Another caller may have renewed it while we waited for the lock.
            if (
                self._token is not None
                and self._token != stale_token
                and (
                    self._token_expiry is None
                    or self._token_expiry - datetime.now(UTC) > _TOKEN_MARGIN
                )
            ):
                return
            await self.login()

    async def _raw_request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        timeout: float,
        token: str | None = None,
    ) -> tuple[int, Any]:
        headers = dict(_HEADERS)
        if token:
            headers["Authorization"] = f"Bearer {token}"
        try:
            async with self._session.request(
                method,
                f"{self._base_url}{path}",
                params=params,
                json=json,
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=timeout),
            ) as resp:
                text = await resp.text()
                try:
                    body: Any = await resp.json(content_type=None) if text else None
                except ValueError:
                    body = text
                return resp.status, body
        except TimeoutError as err:
            raise AlfredTimeoutError(f"{method} {path}: no answer in {timeout}s") from err
        except aiohttp.ClientError as err:
            raise AlfredConnectionError(f"{method} {path}: {err}") from err

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, str] | None = None,
        json: Any = None,
        timeout: float = READ_TIMEOUT,
    ) -> Any:
        """Authenticated request; logs in again once if the session expired.

        Retrying after a 401 is safe even for an opening: the API refused the
        request before doing anything.
        """
        await self._ensure_token()
        token = self._token
        status, body = await self._raw_request(
            method, path, params=params, json=json, timeout=timeout, token=token
        )
        if status == 401:
            _LOGGER.debug("%s %s answered 401, logging in again", method, path)
            await self._ensure_token(stale_token=token)
            status, body = await self._raw_request(
                method, path, params=params, json=json, timeout=timeout, token=self._token
            )
        if status >= 400:
            raise _error_for(status, body)
        return body

    # --- discovery -----------------------------------------------------------

    async def get_devices_raw(self, asset_id: str) -> Any:
        """Raw `GET /devices` for one home (kept for diagnostics)."""
        return await self.request(
            "GET", "/devices", params={**_DEVICE_LIST_PARAMS, "asset_id": asset_id}
        )

    async def get_devices(self, asset_id: str) -> list[AccessDevice]:
        """Everything that opens in one home, shared community devices included."""
        return parse_devices(await self.get_devices_raw(asset_id))

    async def discover_assets(self) -> list[Asset]:
        """Homes this account can use. Empty when no endpoint tells.

        The web app takes the home from its own state, so there is no
        documented listing. Try the candidates in order, read-only.
        """
        candidates: tuple[tuple[str, dict[str, str]], ...] = (
            ("/assets", {"page[number]": "1", "page[size]": "100"}),
            ("/users/me/assets", {}),
            ("/users/me", {}),
        )
        for path, params in candidates:
            try:
                body = await self.request("GET", path, params=params or None)
            except AlfredAuthError:
                raise
            except AlfredSmartError as err:
                _LOGGER.debug("Asset discovery via %s failed: %s", path, err)
                continue
            assets = parse_assets(body)
            if not assets and isinstance(body, dict):
                for key in ("assets", "properties", "homes"):
                    assets = parse_assets({"data": _nested(body, key)})
                    if assets:
                        break
            if assets:
                return assets

        # Last resort: devices listed without a home filter carry their home.
        try:
            body = await self.request("GET", "/devices", params=_DEVICE_LIST_PARAMS)
        except AlfredAuthError:
            raise
        except AlfredSmartError as err:
            _LOGGER.debug("Asset discovery via /devices failed: %s", err)
            return []
        return assets_from_devices(body)

    async def get_gateway(self, gateway_id: str) -> GatewayInfo:
        """Firmware and model of a gateway. Residents can read, not reboot."""
        return parse_gateway(gateway_id, await self.request("GET", f"/gateways/{gateway_id}"))

    async def get_common_areas_raw(self, asset_id: str) -> Any:
        """Raw common-area listing (best effort: endpoint not confirmed)."""
        return await self.request(
            "GET",
            "/common-areas",
            params={"page[number]": "1", "page[size]": "100", "asset_id": asset_id},
        )

    async def get_common_areas(self, asset_id: str) -> list[CommonArea]:
        """Bookable common areas of one home."""
        return parse_common_areas(await self.get_common_areas_raw(asset_id))

    # --- actions -------------------------------------------------------------

    async def open_device(self, gateway_id: str, device_id: str, sensor_id: str = "0") -> Any:
        """Open a portal/garage. Raises on any non-2xx answer.

        Never retried on timeout: the server may already have accepted it, and
        a second try would open the gate twice.
        """
        return await self.request(
            "PATCH",
            "/devices/interact",
            json={
                "data": {
                    "value": "ON",
                    "gateway_id": gateway_id,
                    "device_id": device_id,
                    "sensor_id": sensor_id,
                }
            },
            timeout=INTERACT_TIMEOUT,
        )

    async def open_common_area(self, common_area_id: str, sensor_uuid: str) -> Any:
        """Open the door of a booked common area."""
        return await self.request(
            "POST",
            f"/common-areas/{common_area_id}/open",
            json={"data": {"sensor_uuid": sensor_uuid}},
            timeout=INTERACT_TIMEOUT,
        )

    async def book_common_area(self, common_area_id: str, start: datetime, end: datetime) -> Any:
        """Book a common area. Datetimes must be timezone-aware."""
        return await self.request(
            "POST",
            f"/common-areas/{common_area_id}/booking",
            json={
                "data": {
                    "date_booking_start": int(start.timestamp()),
                    "date_booking_end": int(end.timestamp()),
                }
            },
        )


def _nested(body: dict[str, Any], key: str) -> Any:
    value = body.get(key)
    data = body.get("data")
    if value is None and isinstance(data, dict):
        value = data.get(key)
    return value


def _error_for(status: int, body: Any) -> AlfredSmartError:
    if status == 401:
        return AlfredAuthError(f"Session rejected (HTTP {status})")
    if status == 403:
        return AlfredForbiddenError(status, body)
    if status == 404:
        return AlfredNotFoundError(status, body)
    if status in (502, 503, 504):
        return AlfredGatewayUnreachableError(status, body)
    return AlfredRequestError(status, body)

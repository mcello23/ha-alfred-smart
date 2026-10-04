"""Polling coordinator for Alfred Smart."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import (
    AlfredAuthError,
    AlfredForbiddenError,
    AlfredGatewayUnreachableError,
    AlfredNotFoundError,
    AlfredSmartClient,
    AlfredSmartError,
    AlfredTimeoutError,
)
from .const import (
    CONF_ASSET_ID,
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    SLOW_REFRESH_INTERVAL,
    UPDATE_GRACE_PERIOD,
)
from .models import (
    AccessDevice,
    CommonArea,
    GatewayInfo,
    parse_common_areas,
    parse_devices,
)

_LOGGER = logging.getLogger(__name__)

type AlfredConfigEntry = ConfigEntry[AlfredCoordinator]


class OpenResult(StrEnum):
    """Outcome of the last opening, as shown by the `last_result` sensor."""

    OK = "ok"
    GATEWAY_UNREACHABLE = "gateway_unreachable"
    UNAUTHORIZED = "unauthorized"
    FORBIDDEN = "forbidden"
    TIMEOUT = "timeout"
    ERROR = "error"


@dataclass(slots=True)
class OpenAttempt:
    """When and how the last opening went."""

    result: OpenResult
    at: datetime
    http_status: int | None = None


@dataclass(slots=True)
class AlfredData:
    """Everything the entities read."""

    devices: dict[str, AccessDevice] = field(default_factory=dict)
    gateways: dict[str, GatewayInfo] = field(default_factory=dict)
    common_areas: dict[str, CommonArea] = field(default_factory=dict)
    raw_devices: Any = None
    raw_common_areas: Any = None


def classify_error(err: Exception) -> tuple[OpenResult, int | None]:
    """Map an API exception to the result shown to the user."""
    status = getattr(err, "status", None)
    if isinstance(err, AlfredGatewayUnreachableError):
        return OpenResult.GATEWAY_UNREACHABLE, status
    if isinstance(err, AlfredAuthError):
        return OpenResult.UNAUTHORIZED, 401
    if isinstance(err, AlfredForbiddenError):
        return OpenResult.FORBIDDEN, status
    if isinstance(err, AlfredTimeoutError):
        return OpenResult.TIMEOUT, None
    return OpenResult.ERROR, status


class AlfredCoordinator(DataUpdateCoordinator[AlfredData]):
    """Reads the devices of one home every few minutes."""

    config_entry: AlfredConfigEntry

    def __init__(
        self, hass: HomeAssistant, entry: AlfredConfigEntry, client: AlfredSmartClient
    ) -> None:
        minutes = entry.options.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL)
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN} {entry.data[CONF_ASSET_ID]}",
            update_interval=timedelta(minutes=minutes),
        )
        self.client = client
        self.asset_id: str = entry.data[CONF_ASSET_ID]
        self.last_success: datetime | None = None
        self.missed_polls = 0
        self.open_attempts: dict[str, OpenAttempt] = {}
        # Device registry ids of the parents, for `via_device_id`.
        self.hub_device_id: str | None = None
        self.gateway_device_ids: dict[str, str] = {}
        self._slow_refreshed_at: datetime | None = None
        # Unconfirmed endpoint: stop asking once it says it does not exist.
        self._common_areas_supported = True

    async def _async_update_data(self) -> AlfredData:
        try:
            raw_devices = await self.client.get_devices_raw(self.asset_id)
        except AlfredAuthError as err:
            raise ConfigEntryAuthFailed(str(err)) from err
        except AlfredSmartError as err:
            return self._hold_or_fail(err)

        previous = self.data or AlfredData()
        data = AlfredData(
            devices={device.key: device for device in parse_devices(raw_devices)},
            gateways=dict(previous.gateways),
            common_areas=dict(previous.common_areas),
            raw_devices=raw_devices,
            raw_common_areas=previous.raw_common_areas,
        )

        now = dt_util.utcnow()
        new_gateways = {d.gateway_id for d in data.devices.values()} - set(data.gateways)
        if (
            new_gateways
            or self._slow_refreshed_at is None
            or now - self._slow_refreshed_at >= SLOW_REFRESH_INTERVAL
        ):
            await self._refresh_slow(data)
            self._slow_refreshed_at = now

        self.last_success = now
        self.missed_polls = 0
        return data

    def _hold_or_fail(self, err: AlfredSmartError) -> AlfredData:
        """Keep the last data through short outages instead of flapping."""
        self.missed_polls += 1
        if (
            self.data is not None
            and self.last_success is not None
            and dt_util.utcnow() - self.last_success < UPDATE_GRACE_PERIOD
        ):
            _LOGGER.debug(
                "Poll failed (%s), keeping data from %s (%s missed)",
                err,
                self.last_success,
                self.missed_polls,
            )
            return self.data
        raise UpdateFailed(f"Alfred Smart did not answer: {err}") from err

    async def _refresh_slow(self, data: AlfredData) -> None:
        """Gateway firmware and common areas; failures here are not fatal."""
        for gateway_id in {d.gateway_id for d in data.devices.values()}:
            try:
                data.gateways[gateway_id] = await self.client.get_gateway(gateway_id)
            except AlfredSmartError as err:
                _LOGGER.debug("Could not read gateway %s: %s", gateway_id, err)
                data.gateways.setdefault(
                    gateway_id, GatewayInfo(id=gateway_id, firmware=None, model=None)
                )

        if not self._common_areas_supported:
            return
        try:
            raw = await self.client.get_common_areas_raw(self.asset_id)
        except AlfredNotFoundError:
            _LOGGER.debug("No common-area listing for this account")
            self._common_areas_supported = False
            return
        except AlfredSmartError as err:
            _LOGGER.debug("Could not list common areas: %s", err)
            return

        data.raw_common_areas = raw
        data.common_areas = {area.id: area for area in parse_common_areas(raw)}

    def record_attempt(self, key: str, err: Exception | None) -> None:
        """Remember the outcome of an opening and refresh the sensors."""
        if err is None:
            attempt = OpenAttempt(OpenResult.OK, dt_util.utcnow())
        else:
            result, status = classify_error(err)
            attempt = OpenAttempt(result, dt_util.utcnow(), status)
        self.open_attempts[key] = attempt
        self.async_update_listeners()

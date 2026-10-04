"""Services: book and open common areas (gym, social club...)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

import voluptuous as vol
from homeassistant.config_entries import ConfigEntryState
from homeassistant.core import (
    HomeAssistant,
    ServiceCall,
    ServiceResponse,
    SupportsResponse,
    callback,
)
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import config_validation as cv
from homeassistant.util import dt as dt_util

from .api import AlfredSmartError
from .const import (
    ATTR_COMMON_AREA_ID,
    ATTR_CONFIG_ENTRY_ID,
    ATTR_END,
    ATTR_SENSOR_UUID,
    ATTR_START,
    DOMAIN,
    SERVICE_BOOK_COMMON_AREA,
    SERVICE_OPEN_COMMON_AREA,
)
from .coordinator import AlfredConfigEntry, AlfredCoordinator, classify_error

_BASE_SCHEMA = {
    vol.Optional(ATTR_CONFIG_ENTRY_ID): cv.string,
    vol.Required(ATTR_COMMON_AREA_ID): cv.string,
}

OPEN_SCHEMA = vol.Schema({**_BASE_SCHEMA, vol.Optional(ATTR_SENSOR_UUID): cv.string})
BOOK_SCHEMA = vol.Schema(
    {
        **_BASE_SCHEMA,
        vol.Required(ATTR_START): cv.datetime,
        vol.Required(ATTR_END): cv.datetime,
    }
)


def _coordinator(hass: HomeAssistant, call: ServiceCall) -> AlfredCoordinator:
    """The home the call is about: explicit, or the only one configured."""
    entries: list[AlfredConfigEntry] = [
        entry
        for entry in hass.config_entries.async_entries(DOMAIN)
        if entry.state is ConfigEntryState.LOADED
    ]
    if entry_id := call.data.get(ATTR_CONFIG_ENTRY_ID):
        entries = [entry for entry in entries if entry.entry_id == entry_id]
        if not entries:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="entry_not_found",
                translation_placeholders={"entry_id": entry_id},
            )
    if not entries:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="not_loaded")
    if len(entries) > 1:
        raise ServiceValidationError(translation_domain=DOMAIN, translation_key="entry_required")
    return entries[0].runtime_data


def _aware(value: datetime) -> datetime:
    """Naive times from the UI are local to the Home Assistant instance."""
    if value.tzinfo is None:
        return value.replace(tzinfo=dt_util.get_default_time_zone())
    return value


def _api_error(err: AlfredSmartError, area_id: str) -> HomeAssistantError:
    result, status = classify_error(err)
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=f"common_area_{result.value}",
        translation_placeholders={"name": area_id, "status": str(status or "-")},
    )


def _as_response(body: Any) -> ServiceResponse:
    return body if isinstance(body, dict) else {"response": body}


@callback
def async_setup_services(hass: HomeAssistant) -> None:
    """Register the services once, for every home."""

    async def _open(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        area_id: str = call.data[ATTR_COMMON_AREA_ID]
        sensor_uuid = call.data.get(ATTR_SENSOR_UUID)
        if sensor_uuid is None and coordinator.data is not None:
            area = coordinator.data.common_areas.get(area_id)
            sensor_uuid = area.sensor_uuid if area else None
        if not sensor_uuid:
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="sensor_uuid_required",
                translation_placeholders={"area": area_id},
            )
        try:
            body = await coordinator.client.open_common_area(area_id, sensor_uuid)
        except AlfredSmartError as err:
            raise _api_error(err, area_id) from err
        return _as_response(body) if call.return_response else None

    async def _book(call: ServiceCall) -> ServiceResponse:
        coordinator = _coordinator(hass, call)
        area_id: str = call.data[ATTR_COMMON_AREA_ID]
        start = _aware(call.data[ATTR_START])
        end = _aware(call.data[ATTR_END])
        if end <= start:
            raise ServiceValidationError(
                translation_domain=DOMAIN, translation_key="end_before_start"
            )
        try:
            body = await coordinator.client.book_common_area(area_id, start, end)
        except AlfredSmartError as err:
            raise _api_error(err, area_id) from err
        return _as_response(body) if call.return_response else None

    hass.services.async_register(
        DOMAIN,
        SERVICE_OPEN_COMMON_AREA,
        _open,
        schema=OPEN_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )
    hass.services.async_register(
        DOMAIN,
        SERVICE_BOOK_COMMON_AREA,
        _book,
        schema=BOOK_SCHEMA,
        supports_response=SupportsResponse.OPTIONAL,
    )

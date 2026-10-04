"""Diagnostics: the raw API answers, redacted, to improve discovery."""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant

from .coordinator import AlfredConfigEntry

TO_REDACT = {
    CONF_EMAIL,
    CONF_PASSWORD,
    "token",
    "access_token",
    "refresh_token",
    "address",
    "street",
    "phone",
    "mobile",
    "first_name",
    "last_name",
    "full_name",
    "user_email",
    "latitude",
    "longitude",
    "lat",
    "lng",
    "serial",
    "serial_number",
    "mac",
    "mac_address",
    "ip",
    "pin",
    "code",
}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: AlfredConfigEntry
) -> dict[str, Any]:
    """Entry, parsed model and raw responses."""
    coordinator = entry.runtime_data
    data = coordinator.data
    return {
        "entry": async_redact_data(dict(entry.data), TO_REDACT),
        "options": dict(entry.options),
        "token_expiry": (
            coordinator.client.token_expiry.isoformat() if coordinator.client.token_expiry else None
        ),
        "last_success": (
            coordinator.last_success.isoformat() if coordinator.last_success else None
        ),
        "missed_polls": coordinator.missed_polls,
        "parsed": {
            "devices": [
                {k: v for k, v in asdict(d).items() if k != "raw"}
                for d in (data.devices.values() if data else [])
            ],
            "gateways": [
                {k: v for k, v in asdict(g).items() if k != "raw"}
                for g in (data.gateways.values() if data else [])
            ],
            "common_areas": [
                {k: v for k, v in asdict(a).items() if k != "raw"}
                for a in (data.common_areas.values() if data else [])
            ],
        },
        "open_attempts": {
            key: {
                "result": a.result.value,
                "at": a.at.isoformat(),
                "http_status": a.http_status,
            }
            for key, a in coordinator.open_attempts.items()
        },
        "raw": {
            "devices": async_redact_data(data.raw_devices, TO_REDACT) if data else None,
            "gateways": {
                gid: async_redact_data(g.raw, TO_REDACT)
                for gid, g in (data.gateways.items() if data else [])
            },
            "common_areas": (async_redact_data(data.raw_common_areas, TO_REDACT) if data else None),
        },
    }

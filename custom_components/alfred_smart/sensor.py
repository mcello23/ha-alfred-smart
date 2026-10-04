"""Sensor with the outcome of the last opening of each device.

`enabled: true` in the device list does not mean the portal works: a gateway
can be offline while Alfred still lists it as enabled. Only an opening finds
out, so its result is worth keeping in sight.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity
from homeassistant.util import dt as dt_util

from .coordinator import (
    AlfredConfigEntry,
    AlfredCoordinator,
    OpenAttempt,
    OpenResult,
)
from .entity import AlfredAccessEntity
from .models import AccessDevice

PARALLEL_UPDATES = 0

ATTR_LAST_ATTEMPT = "last_attempt"
ATTR_HTTP_STATUS = "http_status"


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AlfredConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One `last_result` sensor per device."""
    coordinator = entry.runtime_data
    known: set[str] = set()

    @callback
    def _add_new() -> None:
        new = [
            AlfredLastResultSensor(coordinator, device)
            for key, device in coordinator.data.devices.items()
            if key not in known
        ]
        known.update(sensor.device_key for sensor in new)
        if new:
            async_add_entities(new)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


class AlfredLastResultSensor(AlfredAccessEntity, SensorEntity, RestoreEntity):
    """ok / gateway_unreachable / unauthorized / forbidden / timeout / error."""

    _attr_translation_key = "last_result"
    _attr_device_class = SensorDeviceClass.ENUM

    def __init__(self, coordinator: AlfredCoordinator, device: AccessDevice) -> None:
        super().__init__(coordinator, device, "last_result")
        self._attr_options = [result.value for result in OpenResult]
        self.device_key = device.key

    async def async_added_to_hass(self) -> None:
        """Bring the last result back after a restart."""
        await super().async_added_to_hass()
        if self.device_key in self.coordinator.open_attempts:
            return
        last = await self.async_get_last_state()
        if last is None or last.state not in self._attr_options:
            return
        at = dt_util.parse_datetime(str(last.attributes.get(ATTR_LAST_ATTEMPT, "")))
        status = last.attributes.get(ATTR_HTTP_STATUS)
        self.coordinator.open_attempts[self.device_key] = OpenAttempt(
            OpenResult(last.state),
            at or dt_util.utcnow(),
            int(status) if isinstance(status, int | str) and str(status).isdigit() else None,
        )

    @property
    def available(self) -> bool:
        """The last result stays readable even when the cloud is down."""
        return self.device is not None

    @property
    def _attempt(self) -> OpenAttempt | None:
        return self.coordinator.open_attempts.get(self.device_key)

    @property
    def native_value(self) -> str | None:
        """Outcome of the last opening; unknown until the first one."""
        attempt = self._attempt
        return attempt.result.value if attempt else None

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """When it happened and the raw HTTP status, for diagnosing."""
        attempt = self._attempt
        if attempt is None:
            return {}
        at: datetime = attempt.at
        return {ATTR_LAST_ATTEMPT: at.isoformat(), ATTR_HTTP_STATUS: attempt.http_status}

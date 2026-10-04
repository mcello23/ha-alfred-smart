"""Buttons that open portals, garages and common areas.

A button and not a lock or cover: Alfred only exposes a momentary "open"
pulse, never whether the gate is open or closed.
"""

from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import AlfredAuthError, AlfredSmartError
from .const import DOMAIN
from .coordinator import AlfredConfigEntry, AlfredCoordinator, classify_error
from .entity import AlfredAccessEntity, AlfredCommonAreaEntity
from .models import AccessDevice, CommonArea

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AlfredConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Create a button per device, and keep adding them as Alfred lists more."""
    coordinator = entry.runtime_data
    known_devices: set[str] = set()
    known_areas: set[str] = set()

    @callback
    def _add_new() -> None:
        entities: list[ButtonEntity] = []
        for key, device in coordinator.data.devices.items():
            if key not in known_devices:
                known_devices.add(key)
                entities.append(AlfredOpenButton(coordinator, device))
        for area_id, area in coordinator.data.common_areas.items():
            if area_id not in known_areas and area.sensor_uuid:
                known_areas.add(area_id)
                entities.append(AlfredOpenCommonAreaButton(coordinator, area))
        if entities:
            async_add_entities(entities)

    _add_new()
    entry.async_on_unload(coordinator.async_add_listener(_add_new))


def _raise_for(err: AlfredSmartError, name: str) -> HomeAssistantError:
    result, status = classify_error(err)
    return HomeAssistantError(
        translation_domain=DOMAIN,
        translation_key=f"open_{result.value}",
        translation_placeholders={"name": name, "status": str(status or "-")},
    )


class AlfredOpenButton(AlfredAccessEntity, ButtonEntity):
    """Sends the opening pulse to one portal / garage / door."""

    def __init__(self, coordinator: AlfredCoordinator, device: AccessDevice) -> None:
        super().__init__(coordinator, device, "open")
        self._attr_translation_key = f"open_{device.kind.value}"

    async def async_press(self) -> None:
        """Open it, and fail loudly when Alfred says it did not."""
        device = self.device
        if device is None:
            return
        try:
            await self.coordinator.client.open_device(
                device.gateway_id, device.device_id, device.sensor_id
            )
        except AlfredSmartError as err:
            self.coordinator.record_attempt(device.key, err)
            if isinstance(err, AlfredAuthError):
                self.coordinator.config_entry.async_start_reauth(self.hass)
            raise _raise_for(err, device.name) from err
        self.coordinator.record_attempt(device.key, None)


class AlfredOpenCommonAreaButton(AlfredCommonAreaEntity, ButtonEntity):
    """Opens the door of a common area (it usually needs a booking)."""

    _attr_translation_key = "open_common_area"

    def __init__(self, coordinator: AlfredCoordinator, area: CommonArea) -> None:
        super().__init__(coordinator, area, "open")

    async def async_press(self) -> None:
        """Open the area's door."""
        area = self.area
        if area is None or area.sensor_uuid is None:
            return
        try:
            await self.coordinator.client.open_common_area(area.id, area.sensor_uuid)
        except AlfredSmartError as err:
            if isinstance(err, AlfredAuthError):
                self.coordinator.config_entry.async_start_reauth(self.hass)
            raise _raise_for(err, area.name) from err

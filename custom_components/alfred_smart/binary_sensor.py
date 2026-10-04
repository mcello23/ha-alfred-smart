"""Connectivity of the Alfred Smart cloud for one home."""

from __future__ import annotations

from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import AlfredConfigEntry, AlfredCoordinator
from .entity import AlfredHubEntity

PARALLEL_UPDATES = 0


async def async_setup_entry(
    hass: HomeAssistant,
    entry: AlfredConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """One connectivity sensor per home."""
    async_add_entities([AlfredConnectivitySensor(entry.runtime_data)])


class AlfredConnectivitySensor(AlfredHubEntity, BinarySensorEntity):
    """On while Alfred answers. Survives single missed polls (grace period).

    It reports the cloud, not each gateway: a portal whose gateway is offline
    still shows up as enabled. See the `last_result` sensor for that.
    """

    _attr_translation_key = "cloud_connection"
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator: AlfredCoordinator) -> None:
        super().__init__(coordinator, "cloud_connection")

    @property
    def available(self) -> bool:
        """Always available: being off is the information."""
        return True

    @property
    def is_on(self) -> bool:
        """Whether the last poll (or one within the grace period) worked."""
        return self.coordinator.last_update_success

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Last successful poll and how many were missed since."""
        last = self.coordinator.last_success
        return {
            "last_success": last.isoformat() if last else None,
            "missed_polls": self.coordinator.missed_polls,
            "devices": len(self.coordinator.data.devices) if self.coordinator.data else 0,
        }

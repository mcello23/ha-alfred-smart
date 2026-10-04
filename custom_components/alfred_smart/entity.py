"""Base entities for Alfred Smart."""

from __future__ import annotations

from typing import Any

from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, MANUFACTURER
from .coordinator import AlfredCoordinator
from .models import AccessDevice, CommonArea, DeviceKind

_KIND_MODEL = {
    DeviceKind.GARAGE: "Garage door",
    DeviceKind.GATE: "Gate",
    DeviceKind.DOOR: "Door",
    DeviceKind.OTHER: "Access",
}


# Home Assistant 2026.8 links child devices by registry id (`via_device_id`)
# and deprecates the identifier tuple (`via_device`, removed in 2027.8).
_HAS_VIA_DEVICE_ID = "via_device_id" in DeviceInfo.__annotations__


def via_device(registry_id: str | None, identifier: tuple[str, str]) -> dict[str, Any]:
    """The parent link in whichever form this Home Assistant understands."""
    if _HAS_VIA_DEVICE_ID:
        return {"via_device_id": registry_id} if registry_id else {}
    return {"via_device": identifier}


def device_identifier(asset_id: str, key: str) -> tuple[str, str]:
    """Device registry identifier of an access device."""
    return (DOMAIN, f"{asset_id}_{key}")


def gateway_identifier(asset_id: str, gateway_id: str) -> tuple[str, str]:
    """Device registry identifier of a gateway."""
    return (DOMAIN, f"{asset_id}_gw_{gateway_id}")


def common_area_identifier(asset_id: str, area_id: str) -> tuple[str, str]:
    """Device registry identifier of a common area."""
    return (DOMAIN, f"{asset_id}_area_{area_id}")


class AlfredHubEntity(CoordinatorEntity[AlfredCoordinator]):
    """Entity of the home itself (the cloud connection)."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AlfredCoordinator, suffix: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.asset_id}_{suffix}"
        self._attr_device_info = DeviceInfo(identifiers={(DOMAIN, coordinator.asset_id)})


class AlfredAccessEntity(CoordinatorEntity[AlfredCoordinator]):
    """Entity of one portal / garage / door."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AlfredCoordinator, device: AccessDevice, suffix: str) -> None:
        super().__init__(coordinator)
        self._key = device.key
        asset_id = coordinator.asset_id
        self._attr_unique_id = f"{asset_id}_{device.key}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={device_identifier(asset_id, device.key)},
            manufacturer=MANUFACTURER,
            model=_KIND_MODEL[device.kind],
            name=device.name,
            **via_device(
                coordinator.gateway_device_ids.get(device.gateway_id),
                gateway_identifier(asset_id, device.gateway_id),
            ),
        )

    @property
    def device(self) -> AccessDevice | None:
        """Latest data of this device; None once Alfred stops listing it."""
        return self.coordinator.data.devices.get(self._key)

    @property
    def available(self) -> bool:
        """Unavailable when the cloud is down or the building disabled it."""
        device = self.device
        return super().available and device is not None and device.enabled


class AlfredCommonAreaEntity(CoordinatorEntity[AlfredCoordinator]):
    """Entity of one bookable common area."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: AlfredCoordinator, area: CommonArea, suffix: str) -> None:
        super().__init__(coordinator)
        self._area_id = area.id
        asset_id = coordinator.asset_id
        self._attr_unique_id = f"{asset_id}_area_{area.id}_{suffix}"
        self._attr_device_info = DeviceInfo(
            identifiers={common_area_identifier(asset_id, area.id)},
            manufacturer=MANUFACTURER,
            model="Common area",
            name=area.name,
            **via_device(coordinator.hub_device_id, (DOMAIN, asset_id)),
        )

    @property
    def area(self) -> CommonArea | None:
        """Latest data of this area."""
        return self.coordinator.data.common_areas.get(self._area_id)

    @property
    def available(self) -> bool:
        """Unavailable once the area disappears from the listing."""
        area = self.area
        return super().available and area is not None and area.sensor_uuid is not None

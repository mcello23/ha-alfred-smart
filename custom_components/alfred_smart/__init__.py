"""The Alfred Smart integration: open portals, garages and common areas."""

from __future__ import annotations

from homeassistant.const import CONF_EMAIL, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant, callback
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.typing import ConfigType

from .api import AlfredAuthError, AlfredSmartClient, AlfredSmartError
from .const import CONF_ASSET_ID, CONF_ASSET_NAME, DOMAIN, MANUFACTURER
from .coordinator import AlfredConfigEntry, AlfredCoordinator
from .entity import (
    common_area_identifier,
    device_identifier,
    gateway_identifier,
    via_device,
)
from .services import async_setup_services

PLATFORMS: list[Platform] = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.SENSOR]

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the integration-wide services."""
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: AlfredConfigEntry) -> bool:
    """Log in, read the home once and create the entities."""
    client = AlfredSmartClient(
        async_get_clientsession(hass), entry.data[CONF_EMAIL], entry.data[CONF_PASSWORD]
    )
    try:
        await client.login()
    except AlfredAuthError as err:
        raise ConfigEntryAuthFailed(str(err)) from err
    except AlfredSmartError as err:
        raise ConfigEntryNotReady(str(err)) from err

    coordinator = AlfredCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()
    entry.runtime_data = coordinator

    _register_hub(hass, entry)
    _sync_gateway_devices(hass, entry)
    entry.async_on_unload(
        coordinator.async_add_listener(lambda: _sync_gateway_devices(hass, entry))
    )
    entry.async_on_unload(entry.add_update_listener(_async_reload_on_options))

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: AlfredConfigEntry) -> bool:
    """Unload a config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_reload_on_options(hass: HomeAssistant, entry: AlfredConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_remove_config_entry_device(
    hass: HomeAssistant, entry: AlfredConfigEntry, device_entry: dr.DeviceEntry
) -> bool:
    """Allow deleting a device only once Alfred no longer lists it."""
    coordinator = entry.runtime_data
    asset_id = coordinator.asset_id
    current = {(DOMAIN, asset_id)}
    if coordinator.data is not None:
        current |= {device_identifier(asset_id, key) for key in coordinator.data.devices}
        current |= {gateway_identifier(asset_id, gw) for gw in coordinator.data.gateways}
        current |= {
            common_area_identifier(asset_id, area) for area in coordinator.data.common_areas
        }
    return not (device_entry.identifiers & current)


def _register_hub(hass: HomeAssistant, entry: AlfredConfigEntry) -> None:
    asset_id = entry.data[CONF_ASSET_ID]
    hub = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={(DOMAIN, asset_id)},
        manufacturer=MANUFACTURER,
        name=entry.data.get(CONF_ASSET_NAME) or entry.title,
        model="Cloud",
        entry_type=dr.DeviceEntryType.SERVICE,
        configuration_url="https://app.alfredsmart.com/",
    )
    entry.runtime_data.hub_device_id = hub.id


@callback
def _sync_gateway_devices(hass: HomeAssistant, entry: AlfredConfigEntry) -> None:
    """Gateways exist before the devices that point at them (`via_device_id`)."""
    coordinator = entry.runtime_data
    if coordinator.data is None:
        return
    registry = dr.async_get(hass)
    asset_id = coordinator.asset_id
    for gateway in coordinator.data.gateways.values():
        device = registry.async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={gateway_identifier(asset_id, gateway.id)},
            manufacturer=MANUFACTURER,
            model=gateway.model or "Gateway",
            name=f"Gateway {gateway.id}",
            sw_version=gateway.firmware,
            serial_number=gateway.id,
            **via_device(coordinator.hub_device_id, (DOMAIN, asset_id)),
        )
        coordinator.gateway_device_ids[gateway.id] = device.id

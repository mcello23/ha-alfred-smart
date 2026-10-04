"""End to end inside Home Assistant: config flow, entities, opening errors."""

from __future__ import annotations

import base64
import json
import time
from datetime import timedelta

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from aiohttp import ClientConnectionError
from freezegun.api import FrozenDateTimeFactory
from homeassistant.config_entries import SOURCE_USER, ConfigEntryState
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.exceptions import HomeAssistantError, ServiceValidationError
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from pytest_homeassistant_custom_component.test_util.aiohttp import (
    AiohttpClientMocker,
    AiohttpClientMockResponse,
)
from yarl import URL

from custom_components.alfred_smart.const import CONF_ASSET_ID, CONF_ASSET_NAME, DOMAIN

BASE = "https://services.alfredsmartdata.com"


def _jwt() -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': int(time.time()) + 86400})}.sig"


def _access(device: str, gateway: str) -> str:
    return base64.b64encode(f"0######{device}######{gateway}".encode()).decode()


DEVICES = {
    "data": [
        {
            "asset_id": "HOME1",
            "enabled": True,
            "sensors": [{"name": "Portal 3"}],
            "access_sensor_id": _access("101", "gwok"),
        },
        {
            "asset_id": "HOME1",
            "enabled": True,
            "sensors": [{"name": "Garaje Entrada"}],
            "access_sensor_id": _access("103", "gwdead"),
        },
        {
            "asset_id": "HOME1",
            "enabled": False,
            "sensors": [{"name": "Portal 9"}],
            "access_sensor_id": _access("109", "gwok"),
        },
    ]
}


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Load the integration from custom_components/."""
    return


async def _interact(method: str, url: URL, data: dict) -> AiohttpClientMockResponse:
    status = 502 if data["data"]["gateway_id"] == "gwdead" else 200
    return AiohttpClientMockResponse(method, url, status=status, json={"data": {}})


def mock_alfred(aioclient_mock: AiohttpClientMocker) -> None:
    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    for path in ("/assets", "/users/me/assets", "/users/me", "/common-areas"):
        aioclient_mock.get(f"{BASE}{path}", status=404, json={})
    aioclient_mock.get(f"{BASE}/devices", json=DEVICES)
    aioclient_mock.get(f"{BASE}/gateways/gwok", json={"data": {"firmware_version": "3.35.0"}})
    aioclient_mock.get(f"{BASE}/gateways/gwdead", json={"data": {"firmware_version": "3.34.0"}})
    aioclient_mock.patch(f"{BASE}/devices/interact", side_effect=_interact)


def _entry() -> MockConfigEntry:
    return MockConfigEntry(
        domain=DOMAIN,
        unique_id="HOME1",
        title="Casa",
        data={
            CONF_EMAIL: "me@example.com",
            CONF_PASSWORD: "secret",
            CONF_ASSET_ID: "HOME1",
            CONF_ASSET_NAME: "Casa",
        },
    )


async def test_config_flow_discovers_home(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_alfred(aioclient_mock)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: " me@example.com ", CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["result"].unique_id == "HOME1"
    assert result["data"][CONF_EMAIL] == "me@example.com"
    await hass.async_block_till_done()


async def test_config_flow_bad_password(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{BASE}/users/login", status=401, json={})
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "wrong"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_auth"}


async def test_entities_and_opening(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_alfred(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED

    registry = er.async_get(hass)
    portal = registry.async_get_entity_id("button", DOMAIN, "HOME1_gwok_101_open")
    garage = registry.async_get_entity_id("button", DOMAIN, "HOME1_gwdead_103_open")
    disabled = registry.async_get_entity_id("button", DOMAIN, "HOME1_gwok_109_open")
    garage_result = registry.async_get_entity_id("sensor", DOMAIN, "HOME1_gwdead_103_last_result")
    connection = registry.async_get_entity_id("binary_sensor", DOMAIN, "HOME1_cloud_connection")
    assert portal and garage and disabled and garage_result and connection

    assert registry.async_get(garage).translation_key == "open_garage"
    assert hass.states.get(disabled).state == "unavailable"
    assert hass.states.get(connection).state == "on"
    assert hass.states.get(garage_result).state == "unknown"

    # Gateway firmware lands on the gateway device.
    gateway = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "HOME1_gw_gwdead"), entry.entry_id
    )
    assert gateway is not None and gateway.sw_version == "3.34.0"

    await hass.services.async_call("button", "press", {"entity_id": portal}, blocking=True)
    assert (
        hass.states.get(
            registry.async_get_entity_id("sensor", DOMAIN, "HOME1_gwok_101_last_result")
        ).state
        == "ok"
    )

    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call("button", "press", {"entity_id": garage}, blocking=True)
    assert err.value.translation_key == "open_gateway_unreachable"
    assert "gateway" in str(err.value)
    state = hass.states.get(garage_result)
    assert state.state == "gateway_unreachable"
    assert state.attributes["http_status"] == 502


async def test_one_missed_poll_is_not_an_outage(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    mock_alfred(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    registry = er.async_get(hass)
    portal = registry.async_get_entity_id("button", DOMAIN, "HOME1_gwok_101_open")
    connection = registry.async_get_entity_id("binary_sensor", DOMAIN, "HOME1_cloud_connection")

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    aioclient_mock.get(f"{BASE}/devices", exc=ClientConnectionError())

    # One failed poll: data held, nothing goes unavailable.
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(portal).state != "unavailable"
    assert hass.states.get(connection).state == "on"

    # Past the grace period: now it is an outage.
    for _ in range(3):
        freezer.tick(timedelta(minutes=6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(portal).state == "unavailable"
    assert hass.states.get(connection).state == "off"


async def test_config_flow_asks_for_home_code_when_not_discovered(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    for path in ("/assets", "/users/me/assets", "/users/me"):
        aioclient_mock.get(f"{BASE}{path}", status=404, json={})
    # Devices without `asset_id`: nothing to infer the home from.
    aioclient_mock.get(f"{BASE}/devices", json={"data": []})

    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_EMAIL: "me@example.com", CONF_PASSWORD: "secret"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "asset_manual"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_ASSET_ID: " HOME2 "}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_ASSET_ID] == "HOME2"
    await hass.async_block_till_done()


async def test_reauth(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_alfred(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], {CONF_PASSWORD: "new-secret"}
    )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new-secret"


async def test_book_common_area(hass: HomeAssistant, aioclient_mock: AiohttpClientMocker) -> None:
    mock_alfred(aioclient_mock)
    aioclient_mock.post(f"{BASE}/common-areas/area-1/booking", json={"data": {"id": "b1"}})
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    response = await hass.services.async_call(
        DOMAIN,
        "book_common_area",
        {
            "common_area_id": "area-1",
            "start": "2026-10-05 07:00:00",
            "end": "2026-10-05 07:30:00",
        },
        blocking=True,
        return_response=True,
    )
    assert response == {"data": {"id": "b1"}}
    method, url, body, _ = aioclient_mock.mock_calls[-1]
    assert (method, url.path) == ("POST", "/common-areas/area-1/booking")
    assert body["data"]["date_booking_end"] - body["data"]["date_booking_start"] == 1800

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "book_common_area",
            {"common_area_id": "area-1", "start": "2026-10-05 08:00", "end": "2026-10-05 07:00"},
            blocking=True,
        )


async def test_diagnostics_are_redacted(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_alfred(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    from custom_components.alfred_smart.diagnostics import (
        async_get_config_entry_diagnostics,
    )

    diag = await async_get_config_entry_diagnostics(hass, entry)
    text = json.dumps(diag, default=str)
    assert "secret" not in text and "me@example.com" not in text
    assert len(diag["parsed"]["devices"]) == 3
    assert diag["raw"]["devices"]["data"][0]["sensors"][0]["name"] == "Portal 3"

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


def _device(name: str, room: str, gateway: str, device: int, enabled: bool = True) -> dict:
    """A `GET /devices` entry in the live API's shape (fake ids)."""
    return {
        "asset_id": "HOME1",
        "device_id": device,
        "enabled": enabled,
        "gateway_uuid": gateway,
        "sensors": [
            {
                "access_sensor_id": _access(str(device), gateway),
                "device_id": device,
                "name": name,
                "room": room,
                "sensor_id": 0,
                "shared_with_asset_list": ["NEIGHBOUR1", "NEIGHBOUR2"],
            }
        ],
    }


DEVICES = {
    "data": [
        _device("Portal 3", "Portales", "gwok", 101),
        # A garage door named after the way it opens: only the room says garage.
        _device("Entrada Ext", "Garaje", "gwdead", 103),
        _device("Portal 9", "Portales", "gwok", 109, enabled=False),
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
    aioclient_mock.get(
        f"{BASE}/gateways/gwok",
        json={
            "data": {
                "firmware_version": "3.35.0",
                "sku": "AM-GZ80X",
                "type": "shared",
                "description": "Portal 3 Comunidad",
            }
        },
    )
    aioclient_mock.get(
        f"{BASE}/gateways/gwdead",
        json={
            "data": {
                "firmware_version": "3.34.0",
                "master_user": "owner-user-id",
                "qr_code": "PAIRING-QR",
                "voice_assistant_user": "voice@example.com",
            }
        },
    )
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
    other = dr.async_get(hass).async_get_device_by_identifier(
        (DOMAIN, "HOME1_gw_gwok"), entry.entry_id
    )
    assert other is not None and other.model == "AM-GZ80X"
    # Named as building management named it; the id only when Alfred gives no name.
    assert other.name == "Portal 3 Comunidad"
    assert gateway.name == "Gateway gwdead"

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
    disabled = registry.async_get_entity_id("button", DOMAIN, "HOME1_gwok_109_open")
    connection = registry.async_get_entity_id("binary_sensor", DOMAIN, "HOME1_cloud_connection")

    aioclient_mock.clear_requests()
    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    aioclient_mock.get(f"{BASE}/devices", exc=ClientConnectionError())
    aioclient_mock.patch(f"{BASE}/devices/interact", side_effect=_interact)

    # One failed poll: data held, nothing goes unavailable.
    freezer.tick(timedelta(minutes=6))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    assert hass.states.get(portal).state != "unavailable"
    assert hass.states.get(connection).state == "on"

    # Past the grace period the connection sensor reports the outage...
    for _ in range(3):
        freezer.tick(timedelta(minutes=6))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert hass.states.get(connection).state == "off"

    # ...but the button stays pressable: reading the list and opening are
    # different requests, and `button.press` would skip an unavailable button
    # without a word. A disabled access stays unavailable.
    assert hass.states.get(portal).state != "unavailable"
    assert hass.states.get(disabled).state == "unavailable"
    await hass.services.async_call("button", "press", {"entity_id": portal}, blocking=True)
    assert any(call[1].path == "/devices/interact" for call in aioclient_mock.mock_calls)


def _logins(aioclient_mock: AiohttpClientMocker) -> int:
    return sum(1 for call in aioclient_mock.mock_calls if call[1].path == "/users/login")


async def test_session_survives_a_restart(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict
) -> None:
    mock_alfred(aioclient_mock)
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert _logins(aioclient_mock) == 1
    key = f"{DOMAIN}.{entry.entry_id}"
    token = hass_storage[key]["data"]["token"]
    assert token

    # Reloading (as a restart does) reuses the saved session.
    assert await hass.config_entries.async_reload(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _logins(aioclient_mock) == 1

    # Removing the entry forgets it.
    assert await hass.config_entries.async_remove(entry.entry_id)
    await hass.async_block_till_done()
    assert key not in hass_storage


async def test_saved_session_rejected_logs_in_again(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, hass_storage: dict
) -> None:
    entry = _entry()
    entry.add_to_hass(hass)
    key = f"{DOMAIN}.{entry.entry_id}"
    hass_storage[key] = {"version": 1, "minor_version": 1, "key": key, "data": {"token": "old"}}

    reads = {"devices": 0}

    async def _devices(method: str, url: URL, data: dict) -> AiohttpClientMockResponse:
        # The saved session was revoked: the first read is refused.
        reads["devices"] += 1
        if reads["devices"] == 1:
            return AiohttpClientMockResponse(method, url, status=401, json={})
        return AiohttpClientMockResponse(method, url, json=DEVICES)

    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    aioclient_mock.get(f"{BASE}/devices", side_effect=_devices)
    aioclient_mock.get(f"{BASE}/common-areas", status=404, json={})
    aioclient_mock.get(f"{BASE}/gateways/gwok", json={"data": {}})
    aioclient_mock.get(f"{BASE}/gateways/gwdead", json={"data": {}})

    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    assert entry.state is ConfigEntryState.LOADED
    assert _logins(aioclient_mock) == 1
    assert hass_storage[key]["data"]["token"] != "old"


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
    aioclient_mock.post(
        f"{BASE}/common-areas/area-busy/booking", status=409, json={"error": "overlapping"}
    )
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
    assert response == {
        "ok": True,
        "result": "ok",
        "status": None,
        "response": {"data": {"id": "b1"}},
    }
    method, url, body, _ = aioclient_mock.mock_calls[-1]
    assert (method, url.path) == ("POST", "/common-areas/area-1/booking")
    assert body["data"]["date_booking_end"] - body["data"]["date_booking_start"] == 1800

    # With a response variable a refusal is an answer, not an exception, so a
    # script can tell "slot taken" from "session rejected".
    busy = {
        "common_area_id": "area-busy",
        "start": "2026-10-05 07:00:00",
        "end": "2026-10-05 07:30:00",
    }
    response = await hass.services.async_call(
        DOMAIN, "book_common_area", busy, blocking=True, return_response=True
    )
    assert response == {
        "ok": False,
        "result": "error",
        "status": 409,
        "response": {"error": "overlapping"},
    }

    # Without one (a dashboard button), it still fails loudly.
    with pytest.raises(HomeAssistantError) as err:
        await hass.services.async_call(DOMAIN, "book_common_area", busy, blocking=True)
    assert err.value.translation_key == "common_area_error"

    with pytest.raises(ServiceValidationError):
        await hass.services.async_call(
            DOMAIN,
            "book_common_area",
            {"common_area_id": "area-1", "start": "2026-10-05 08:00", "end": "2026-10-05 07:00"},
            blocking=True,
        )


async def test_open_common_area_reports_the_gateway(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker
) -> None:
    mock_alfred(aioclient_mock)
    aioclient_mock.post(f"{BASE}/common-areas/gym/open", status=502, json={})
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    response = await hass.services.async_call(
        DOMAIN,
        "open_common_area",
        {"common_area_id": "gym", "sensor_uuid": "reader-1"},
        blocking=True,
        return_response=True,
    )
    assert response["ok"] is False
    assert (response["result"], response["status"]) == ("gateway_unreachable", 502)


async def test_common_area_listing_that_keeps_failing_is_dropped(
    hass: HomeAssistant, aioclient_mock: AiohttpClientMocker, freezer: FrozenDateTimeFactory
) -> None:
    aioclient_mock.post(f"{BASE}/users/login", json={"session": {"token": _jwt()}})
    aioclient_mock.get(f"{BASE}/devices", json=DEVICES)
    aioclient_mock.get(f"{BASE}/gateways/gwok", json={"data": {}})
    aioclient_mock.get(f"{BASE}/gateways/gwdead", json={"data": {}})
    # What a real account answers: 503, every time, never 404.
    aioclient_mock.get(f"{BASE}/common-areas", status=503, json={})
    entry = _entry()
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    def listings() -> int:
        return sum(1 for call in aioclient_mock.mock_calls if call[1].path == "/common-areas")

    assert listings() == 1
    for _ in range(4):
        freezer.tick(timedelta(hours=1, minutes=1))
        async_fire_time_changed(hass)
        await hass.async_block_till_done()
    assert listings() == 3
    assert entry.state is ConfigEntryState.LOADED


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
    # The other homes of the community and the gateway's owner and pairing data.
    for leak in ("NEIGHBOUR1", "owner-user-id", "PAIRING-QR", "voice@example.com"):
        assert leak not in text
    # The home's name is usually its street address.
    assert diag["entry"]["asset_name"] == "**REDACTED**"
    assert len(diag["parsed"]["devices"]) == 3
    assert diag["raw"]["devices"]["data"][0]["sensors"][0]["name"] == "Portal 3"

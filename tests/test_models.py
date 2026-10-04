"""Parsing of Alfred Smart payloads."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

from custom_components.alfred_smart.models import (
    DeviceKind,
    assets_from_devices,
    decode_access_sensor_id,
    extract_token,
    guess_kind,
    jwt_expiry,
    parse_assets,
    parse_common_areas,
    parse_devices,
    parse_gateway,
)


def access_sensor_id(device: str, gateway: str, sensor: str = "0") -> str:
    return base64.b64encode(f"{sensor}######{device}######{gateway}".encode()).decode()


def make_jwt(exp: int) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': exp})}.signature"


def test_decode_access_sensor_id() -> None:
    assert decode_access_sensor_id(access_sensor_id("115", "gw1")) == ("0", "115", "gw1")


def test_decode_access_sensor_id_without_padding() -> None:
    value = access_sensor_id("1", "abc").rstrip("=")
    assert decode_access_sensor_id(value) == ("0", "1", "abc")


def test_decode_access_sensor_id_rejects_garbage() -> None:
    assert decode_access_sensor_id("not base64 !!") is None
    assert decode_access_sensor_id(base64.b64encode(b"only-one-part").decode()) is None
    assert decode_access_sensor_id(None) is None


def test_parse_devices_flat_payload() -> None:
    payload = {
        "data": [
            {
                "enabled": True,
                "sensors": [{"name": "Portal 3"}],
                "access_sensor_id": access_sensor_id("101", "gwA"),
            },
            {
                "enabled": False,
                "sensors": [{"name": "Garaje Entrada Exterior"}],
                "access_sensor_id": access_sensor_id("103", "gwB"),
            },
            # Same pair listed twice (shared + own): only one device.
            {
                "enabled": True,
                "sensors": [{"name": "Portal 3"}],
                "access_sensor_id": access_sensor_id("101", "gwA"),
            },
            # Nothing to open with.
            {"enabled": True, "sensors": [{"name": "Termostato"}]},
        ]
    }
    devices = parse_devices(payload)
    assert [(d.name, d.gateway_id, d.device_id, d.kind, d.enabled) for d in devices] == [
        ("Portal 3", "gwA", "101", DeviceKind.GATE, True),
        ("Garaje Entrada Exterior", "gwB", "103", DeviceKind.GARAGE, False),
    ]
    assert devices[0].key == "gwA_101"


def test_parse_devices_explicit_ids_and_jsonapi() -> None:
    payload = {
        "data": [
            {
                "id": "x",
                "attributes": {"gateway_id": "gw", "device_id": 7, "name": "Puerta piscina"},
            }
        ]
    }
    (device,) = parse_devices(payload)
    assert (device.gateway_id, device.device_id, device.name) == ("gw", "7", "Puerta piscina")
    assert device.kind is DeviceKind.DOOR
    assert device.enabled is True


def test_parse_devices_tolerates_errors() -> None:
    assert parse_devices(None) == []
    assert parse_devices({"error": "unauthorized"}) == []
    assert parse_devices({"data": "nope"}) == []


def test_guess_kind() -> None:
    assert guess_kind("Puerta garaje") is DeviceKind.GARAGE
    assert guess_kind("Portal 1 (Vado)") is DeviceKind.GATE
    assert guess_kind("Entrada P3 Piscina") is DeviceKind.DOOR
    assert guess_kind("Ascensor") is DeviceKind.OTHER


def test_assets() -> None:
    assert [(a.id, a.name) for a in parse_assets({"data": [{"id": "A1", "name": "Casa"}]})] == [
        ("A1", "Casa")
    ]
    devices = {"data": [{"asset_id": "A1"}, {"asset_id": "A1"}, {"asset_id": "B2"}, {}]}
    assert [a.id for a in assets_from_devices(devices)] == ["A1", "B2"]


def test_parse_gateway() -> None:
    info = parse_gateway("gw", {"data": {"firmware_version": "3.35.0", "is_rebootable": True}})
    assert (info.id, info.firmware) == ("gw", "3.35.0")
    assert parse_gateway("gw", "oops").firmware is None


def test_parse_common_areas() -> None:
    payload = {
        "data": [
            {"id": "area-1", "name": "Gimnasio", "sensors": [{"uuid": "sensor-1"}]},
            {"id": "area-2", "name": "Club", "sensor_uuid": "sensor-2"},
            {"id": "area-3", "name": "Sin lector"},
        ]
    }
    areas = parse_common_areas(payload)
    assert [(a.id, a.sensor_uuid) for a in areas] == [
        ("area-1", "sensor-1"),
        ("area-2", "sensor-2"),
        ("area-3", None),
    ]


def test_token_helpers() -> None:
    token = make_jwt(1_800_000_000)
    assert extract_token({"session": {"token": token}}) == token
    assert extract_token({"data": {"session": {"token": token}}}) == token
    assert extract_token({"session": {}}) is None
    assert jwt_expiry(token) == datetime.fromtimestamp(1_800_000_000, UTC)
    assert jwt_expiry("not-a-jwt") is None

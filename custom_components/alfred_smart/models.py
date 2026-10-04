"""Data models and parsers for the Alfred Smart API.

Pure Python on purpose: no Home Assistant and no aiohttp imports, so the
discovery script and the tests can load this file on their own.
"""

from __future__ import annotations

import base64
import binascii
import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

# `access_sensor_id` is base64 of "<sensor>######<device>######<gateway>".
ACCESS_SENSOR_SEPARATOR = "######"


class DeviceKind(StrEnum):
    """What an access device opens, guessed from its name."""

    GARAGE = "garage"
    GATE = "gate"
    DOOR = "door"
    OTHER = "other"


# Checked in order: a "Puerta garaje" is a garage, not a door.
_KIND_KEYWORDS: tuple[tuple[DeviceKind, tuple[str, ...]], ...] = (
    (
        DeviceKind.GARAGE,
        ("garaje", "garatge", "garage", "garagem", "parking", "aparcamiento"),
    ),
    (
        DeviceKind.GATE,
        ("portal", "porton", "portón", "cancela", "verja", "barrera", "gate"),
    ),
    (
        DeviceKind.DOOR,
        ("puerta", "porta", "door", "acceso", "entrada", "piscina", "gimnasio"),
    ),
)


@dataclass(frozen=True, slots=True)
class AccessDevice:
    """Something that opens: a portal, a garage door, a common door."""

    gateway_id: str
    device_id: str
    sensor_id: str
    name: str
    kind: DeviceKind
    enabled: bool
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)

    @property
    def key(self) -> str:
        """Stable identifier: the pair the API needs to open it."""
        return f"{self.gateway_id}_{self.device_id}"


@dataclass(frozen=True, slots=True)
class Asset:
    """A home/community the user has access to."""

    id: str
    name: str


@dataclass(frozen=True, slots=True)
class GatewayInfo:
    """What `GET /gateways/<id>` tells a resident."""

    id: str
    firmware: str | None
    model: str | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


@dataclass(frozen=True, slots=True)
class CommonArea:
    """A bookable space (gym, social club...)."""

    id: str
    name: str
    sensor_uuid: str | None
    raw: dict[str, Any] = field(default_factory=dict, compare=False, repr=False)


def _get(item: dict[str, Any], *keys: str) -> Any:
    """First non-empty value among `keys`, also looking into `attributes`.

    The endpoints seen so far are flat, but some Alfred payloads follow the
    JSON:API shape (`{"id": ..., "attributes": {...}}`).
    """
    attributes = item.get("attributes")
    for source in (item, attributes if isinstance(attributes, dict) else {}):
        for key in keys:
            value = source.get(key)
            if value not in (None, ""):
                return value
    return None


def _items(payload: Any) -> list[dict[str, Any]]:
    """The list of objects inside a response, wherever it is."""
    if isinstance(payload, dict):
        payload = payload.get("data", payload.get("items"))
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    return []


def decode_access_sensor_id(value: Any) -> tuple[str, str, str] | None:
    """Split an `access_sensor_id` into (sensor_id, device_id, gateway_id)."""
    if not isinstance(value, str) or not value:
        return None
    padded = value + "=" * (-len(value) % 4)
    for decoder in (base64.b64decode, base64.urlsafe_b64decode):
        try:
            text = decoder(padded).decode()
        except (binascii.Error, UnicodeDecodeError, ValueError):
            continue
        parts = text.split(ACCESS_SENSOR_SEPARATOR)
        if len(parts) == 3 and all(parts[1:]):
            return parts[0] or "0", parts[1], parts[2]
    return None


def guess_kind(name: str) -> DeviceKind:
    """Garage, gate or door, from words in the name (es/ca/en/pt)."""
    lowered = name.casefold()
    for kind, words in _KIND_KEYWORDS:
        if any(word in lowered for word in words):
            return kind
    return DeviceKind.OTHER


def parse_device(item: dict[str, Any]) -> AccessDevice | None:
    """One entry of `GET /devices`, or None when it cannot be opened."""
    decoded = decode_access_sensor_id(_get(item, "access_sensor_id"))
    gateway_id = _get(item, "gateway_id")
    device_id = _get(item, "device_id")
    sensor_id = "0"
    if decoded is not None:
        sensor_id = decoded[0]
        device_id = device_id or decoded[1]
        gateway_id = gateway_id or decoded[2]
    if not gateway_id or device_id in (None, ""):
        return None

    # The building names the portal on the first sensor ("Portal 3"); the
    # device's own name is often generic.
    sensors = _get(item, "sensors")
    sensor_name = None
    if isinstance(sensors, list) and sensors and isinstance(sensors[0], dict):
        sensor_name = sensors[0].get("name")
    name = str(sensor_name or _get(item, "name", "alias") or f"Alfred {device_id}")
    enabled = _get(item, "enabled")

    return AccessDevice(
        gateway_id=str(gateway_id),
        device_id=str(device_id),
        sensor_id=str(sensor_id),
        name=name.strip(),
        kind=guess_kind(name),
        enabled=True if enabled is None else bool(enabled),
        raw=item,
    )


def parse_devices(payload: Any) -> list[AccessDevice]:
    """All openable devices of a `GET /devices` response, deduplicated."""
    devices: dict[str, AccessDevice] = {}
    for item in _items(payload):
        device = parse_device(item)
        if device is not None and device.key not in devices:
            devices[device.key] = device
    return list(devices.values())


def parse_assets(payload: Any) -> list[Asset]:
    """Homes/communities from a listing endpoint."""
    assets: dict[str, Asset] = {}
    for item in _items(payload):
        asset_id = _get(item, "asset_id", "id", "code")
        if asset_id in (None, ""):
            continue
        name = _get(item, "name", "alias", "title", "address", "asset_name")
        assets.setdefault(str(asset_id), Asset(id=str(asset_id), name=str(name or asset_id)))
    return list(assets.values())


def assets_from_devices(payload: Any) -> list[Asset]:
    """Homes inferred from the `asset_id` carried by each device."""
    assets: dict[str, Asset] = {}
    for item in _items(payload):
        asset_id = _get(item, "asset_id")
        if asset_id in (None, ""):
            continue
        name = _get(item, "asset_name", "asset_alias")
        assets.setdefault(str(asset_id), Asset(id=str(asset_id), name=str(name or asset_id)))
    return list(assets.values())


def parse_gateway(gateway_id: str, payload: Any) -> GatewayInfo:
    """`GET /gateways/<id>`; every field is optional."""
    item = payload.get("data", payload) if isinstance(payload, dict) else {}
    if not isinstance(item, dict):
        item = {}
    firmware = _get(item, "firmware_version", "firmware", "fw_version", "version", "sw_version")
    model = _get(item, "model", "hardware_version", "type")
    return GatewayInfo(
        id=gateway_id,
        firmware=str(firmware) if firmware is not None else None,
        model=str(model) if model is not None else None,
        raw=item,
    )


def parse_common_areas(payload: Any) -> list[CommonArea]:
    """Bookable common areas; the sensor that opens each one, if listed."""
    areas: dict[str, CommonArea] = {}
    for item in _items(payload):
        area_id = _get(item, "id", "uuid", "common_area_id")
        if area_id in (None, ""):
            continue
        sensor_uuid = _get(item, "sensor_uuid")
        sensors = _get(item, "sensors", "access_sensors")
        if sensor_uuid is None and isinstance(sensors, list):
            for sensor in sensors:
                if isinstance(sensor, dict):
                    sensor_uuid = _get(sensor, "uuid", "sensor_uuid", "id")
                    if sensor_uuid:
                        break
        name = _get(item, "name", "title", "alias")
        areas.setdefault(
            str(area_id),
            CommonArea(
                id=str(area_id),
                name=str(name or area_id),
                sensor_uuid=str(sensor_uuid) if sensor_uuid else None,
                raw=item,
            ),
        )
    return list(areas.values())


def extract_token(payload: Any) -> str | None:
    """The JWT inside a `POST /users/login` response."""
    if not isinstance(payload, dict):
        return None
    session = payload.get("session")
    if isinstance(session, dict) and session.get("token"):
        return str(session["token"])
    data = payload.get("data")
    if isinstance(data, dict):
        return extract_token(data)
    return None


def jwt_expiry(token: str) -> datetime | None:
    """`exp` claim of a JWT, without verifying it (it is only a hint)."""
    try:
        segment = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(segment + "=" * (-len(segment) % 4)))
        return datetime.fromtimestamp(int(claims["exp"]), UTC)
    except (IndexError, KeyError, TypeError, ValueError, binascii.Error):
        return None

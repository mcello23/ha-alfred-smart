#!/usr/bin/env python3
"""Read-only discovery of an Alfred Smart account, outside Home Assistant.

Logs in and lists what the integration would create: homes (assets),
portals/garages/doors, gateways and common areas. It only reads: it never
calls `/devices/interact` nor opens or books anything.

    python3 scripts/alfred_discover.py --email you@example.com
    python3 scripts/alfred_discover.py --asset ABCD1234EFGHI --dump alfred.json

The password is asked interactively (or read from ALFRED_PASSWORD). The
`--dump` file is redacted, so it can be attached to an issue.
"""

from __future__ import annotations

import argparse
import getpass
import importlib.util
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1] / "custom_components" / "alfred_smart"


def _load(name: str) -> Any:
    """Import a module of the integration without importing Home Assistant."""
    spec = importlib.util.spec_from_file_location(f"alfred_{name}", ROOT / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module  # dataclasses need the module registered
    spec.loader.exec_module(module)
    return module


models = _load("models")

API = "https://services.alfredsmartdata.com"
ORIGIN = "https://app.alfredsmart.com"
HEADERS = {
    "Accept": "application/json, text/plain, */*",
    "Accept-Version": "1",
    "Content-Type": "application/json;charset=UTF-8",
    "Origin": ORIGIN,
    "Referer": f"{ORIGIN}/",
    "X-App-Web": "true",
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
    ),
}
DEVICE_PARAMS = {
    "page[number]": "1",
    "page[size]": "1000",
    "include_shared": "1",
    "show_visible": "1",
}
REDACT = {
    "email",
    "password",
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


def call(
    method: str,
    path: str,
    token: str | None = None,
    *,
    params: dict[str, str] | None = None,
    body: Any = None,
) -> tuple[int, Any]:
    url = f"{API}{path}"
    if params:
        url += "?" + urllib.parse.urlencode(params)
    headers = dict(HEADERS)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    data = json.dumps(body).encode() if body is not None else None
    request = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(request, timeout=25) as resp:
            status, raw = resp.status, resp.read()
    except urllib.error.HTTPError as err:
        status, raw = err.code, err.read()
    try:
        return status, json.loads(raw) if raw else None
    except ValueError:
        return status, raw.decode(errors="replace")


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: "**REDACTED**" if k in REDACT else redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--email", default=os.environ.get("ALFRED_EMAIL"))
    parser.add_argument("--asset", help="home code (asset_id), if already known")
    parser.add_argument("--dump", type=Path, help="write the redacted raw answers here")
    args = parser.parse_args()

    email = args.email or input("Email: ").strip()
    password = os.environ.get("ALFRED_PASSWORD") or getpass.getpass("Password: ")
    dump: dict[str, Any] = {}

    status, body = call(
        "POST",
        "/users/login",
        body={
            "email": email,
            "password": password,
            "user_device": {
                "device_id": "",
                "device_model": "",
                "vendor": "web",
                "vendor_notification_id": "",
            },
        },
    )
    token = models.extract_token(body)
    if status >= 400 or not token:
        print(f"Login failed: HTTP {status}")
        return 1
    print(f"Login OK, session valid until {models.jwt_expiry(token)}")
    dump["login"] = redact(body)

    print("\n== Homes (assets) ==")
    assets = []
    for path, params in (
        ("/assets", {"page[number]": "1", "page[size]": "100"}),
        ("/users/me/assets", None),
        ("/users/me", None),
    ):
        status, body = call("GET", path, token, params=params)
        dump[f"GET {path}"] = {"status": status, "body": redact(body)}
        found = models.parse_assets(body) if status < 400 else []
        print(f"  GET {path:<18} HTTP {status}  -> {len(found)} found")
        assets = assets or found
    status, body = call("GET", "/devices", token, params=DEVICE_PARAMS)
    from_devices = models.assets_from_devices(body) if status < 400 else []
    print(f"  GET /devices (all)     HTTP {status}  -> {len(from_devices)} found")
    assets = assets or from_devices
    for asset in assets:
        print(f"  - {asset.id}  {asset.name}")

    asset_id = args.asset or (assets[0].id if len(assets) == 1 else None)
    if not asset_id:
        print("\nPass --asset <code> to list the devices of one home.")
        return _write(args.dump, dump)

    print(f"\n== Accesses of {asset_id} ==")
    status, body = call("GET", "/devices", token, params={**DEVICE_PARAMS, "asset_id": asset_id})
    dump["GET /devices?asset_id"] = {"status": status, "body": redact(body)}
    devices = models.parse_devices(body) if status < 400 else []
    print(f"  HTTP {status}, {len(devices)} openable")
    for d in sorted(devices, key=lambda d: d.name):
        flag = "" if d.enabled else "  (disabled)"
        print(
            f"  - {d.name:<32} {d.kind.value:<7} gateway={d.gateway_id} device={d.device_id}{flag}"
        )

    print("\n== Gateways ==")
    for gateway_id in sorted({d.gateway_id for d in devices}):
        status, body = call("GET", f"/gateways/{gateway_id}", token)
        dump[f"GET /gateways/{gateway_id}"] = {"status": status, "body": redact(body)}
        info = models.parse_gateway(gateway_id, body) if status < 400 else None
        print(f"  - {gateway_id}  HTTP {status}  firmware={info.firmware if info else '-'}")

    print("\n== Common areas ==")
    status, body = call(
        "GET",
        "/common-areas",
        token,
        params={"page[number]": "1", "page[size]": "100", "asset_id": asset_id},
    )
    dump["GET /common-areas"] = {"status": status, "body": redact(body)}
    areas = models.parse_common_areas(body) if status < 400 else []
    print(f"  HTTP {status}, {len(areas)} found")
    for area in areas:
        print(f"  - {area.name:<32} id={area.id} sensor_uuid={area.sensor_uuid or '?'}")

    return _write(args.dump, dump)


def _write(path: Path | None, dump: dict[str, Any]) -> int:
    if path:
        path.write_text(json.dumps(dump, indent=2, ensure_ascii=False))
        print(f"\nRedacted answers written to {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

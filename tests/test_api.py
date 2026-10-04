"""The API client against a fake Alfred Smart server."""

from __future__ import annotations

import asyncio
import base64
import json
import time
import unittest
from datetime import UTC, datetime

import aiohttp
import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

from custom_components.alfred_smart.api import (
    AlfredAuthError,
    AlfredForbiddenError,
    AlfredGatewayUnreachableError,
    AlfredSmartClient,
    AlfredTimeoutError,
)
from custom_components.alfred_smart.const import INTERACT_TIMEOUT

# The fake server listens on localhost; the Home Assistant test plugin, when
# installed, blocks sockets unless told otherwise.
pytestmark = pytest.mark.enable_socket


def make_jwt(exp: float) -> str:
    def part(obj: dict) -> str:
        return base64.urlsafe_b64encode(json.dumps(obj).encode()).decode().rstrip("=")

    return f"{part({'alg': 'HS256'})}.{part({'exp': int(exp)})}.sig"


def access_sensor_id(device: str, gateway: str) -> str:
    return base64.b64encode(f"0######{device}######{gateway}".encode()).decode()


class FakeAlfred:
    """Just enough of services.alfredsmartdata.com."""

    def __init__(self) -> None:
        self.logins = 0
        self.valid_tokens: set[str] = set()
        self.interactions: list[dict] = []
        self.token_lifetime = 3600.0
        self.interact_delay = 0.0

    def app(self) -> web.Application:
        app = web.Application()
        app.router.add_post("/users/login", self.login)
        app.router.add_get("/devices", self.devices)
        app.router.add_patch("/devices/interact", self.interact)
        app.router.add_get("/gateways/{id}", self.gateway)
        app.router.add_get("/assets", self.not_found)
        app.router.add_get("/users/me/assets", self.not_found)
        app.router.add_get("/users/me", self.me)
        return app

    def _authorized(self, request: web.Request) -> bool:
        header = request.headers.get("Authorization", "")
        return header.removeprefix("Bearer ") in self.valid_tokens

    async def login(self, request: web.Request) -> web.Response:
        body = await request.json()
        if body["password"] != "secret" or request.headers.get("X-App-Web") != "true":
            return web.json_response({"error": "invalid"}, status=401)
        self.logins += 1
        token = make_jwt(time.time() + self.token_lifetime) + str(self.logins)
        self.valid_tokens.add(token)
        return web.json_response({"session": {"token": token}})

    async def not_found(self, request: web.Request) -> web.Response:
        return web.json_response({"error": "not found"}, status=404)

    async def me(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({}, status=401)
        return web.json_response({"data": {"email": "x"}})

    async def devices(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({}, status=401)
        assert request.query["page[size]"] == "1000"
        asset = request.query.get("asset_id")
        items = [
            {
                "asset_id": "HOME1",
                "enabled": True,
                "sensors": [{"name": "Portal 3"}],
                "access_sensor_id": access_sensor_id("101", "gwok"),
            },
            {
                "asset_id": "HOME1",
                "enabled": True,
                "sensors": [{"name": "Portal 1"}],
                "access_sensor_id": access_sensor_id("115", "gwdead"),
            },
        ]
        if asset not in (None, "HOME1"):
            items = []
        return web.json_response({"data": items})

    async def interact(self, request: web.Request) -> web.Response:
        if not self._authorized(request):
            return web.json_response({}, status=401)
        data = (await request.json())["data"]
        self.interactions.append(data)
        await asyncio.sleep(self.interact_delay)
        if data["gateway_id"] == "gwdead":
            return web.json_response({"error": "bad gateway"}, status=502)
        if data["gateway_id"] == "gwforbidden":
            return web.json_response({}, status=403)
        return web.json_response({"data": {"ok": True}})

    async def gateway(self, request: web.Request) -> web.Response:
        return web.json_response({"data": {"firmware_version": "3.35.0"}})


class ClientTest(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.fake = FakeAlfred()
        self.server = TestServer(self.fake.app())
        await self.server.start_server()
        self.session = aiohttp.ClientSession()
        self.client = self.make_client("secret")

    async def asyncTearDown(self) -> None:
        await self.session.close()
        await self.server.close()

    def make_client(self, password: str) -> AlfredSmartClient:
        return AlfredSmartClient(
            self.session,
            "me@example.com",
            password,
            base_url=str(self.server.make_url("")),
        )

    async def test_bad_password(self) -> None:
        with self.assertRaises(AlfredAuthError):
            await self.make_client("wrong").login()

    async def test_login_reads_expiry(self) -> None:
        await self.client.login()
        assert self.client.token_expiry is not None
        assert self.client.token_expiry > datetime.now(UTC)

    async def test_devices_and_discovery(self) -> None:
        devices = await self.client.get_devices("HOME1")
        assert {d.name for d in devices} == {"Portal 1", "Portal 3"}
        # /assets and /users/me/assets are 404, /users/me has no assets:
        # falls back to the asset_id carried by the devices.
        assets = await self.client.discover_assets()
        assert [a.id for a in assets] == ["HOME1"]
        gateway = await self.client.get_gateway("gwok")
        assert gateway.firmware == "3.35.0"

    async def test_open_ok(self) -> None:
        await self.client.open_device("gwok", "101")
        assert self.fake.interactions == [
            {"value": "ON", "gateway_id": "gwok", "device_id": "101", "sensor_id": "0"}
        ]

    async def test_open_502_is_gateway_unreachable_and_not_retried(self) -> None:
        with self.assertRaises(AlfredGatewayUnreachableError) as ctx:
            await self.client.open_device("gwdead", "115")
        assert ctx.exception.status == 502
        assert len(self.fake.interactions) == 1

    async def test_open_403(self) -> None:
        with self.assertRaises(AlfredForbiddenError):
            await self.client.open_device("gwforbidden", "1")

    async def test_expired_session_logs_in_again_once(self) -> None:
        await self.client.login()
        self.fake.valid_tokens.clear()  # server-side revocation
        await self.client.open_device("gwok", "101")
        assert self.fake.logins == 2
        assert len(self.fake.interactions) == 1

    async def test_token_close_to_expiry_is_renewed_before_use(self) -> None:
        self.fake.token_lifetime = 60  # inside the 10-minute margin
        await self.client.login()
        await self.client.get_devices("HOME1")
        assert self.fake.logins == 2

    async def test_concurrent_requests_share_one_login(self) -> None:
        await asyncio.gather(*(self.client.get_devices("HOME1") for _ in range(5)))
        assert self.fake.logins == 1

    async def test_timeout(self) -> None:
        self.fake.interact_delay = 0.5
        await self.client.login()
        original = self.client._raw_request

        async def short(*args, **kwargs):
            assert kwargs["timeout"] in (INTERACT_TIMEOUT, 0.1)
            kwargs["timeout"] = 0.1
            return await original(*args, **kwargs)

        self.client._raw_request = short  # type: ignore[method-assign]
        with self.assertRaises(AlfredTimeoutError):
            await self.client.open_device("gwok", "101")

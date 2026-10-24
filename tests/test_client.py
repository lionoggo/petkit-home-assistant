"""Client wire-format and authentication tests without contacting PETKIT."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.petkit.client import (
    PetkitAuthError,
    PetkitClient,
)
from custom_components.petkit.protocol import password_digest

pytestmark = [
    pytest.mark.enable_socket,
    pytest.mark.usefixtures("socket_enabled"),
]


async def make_api_server():
    calls: dict[str, int] = {}

    async def login(request: web.Request) -> web.Response:
        calls["login"] = calls.get("login", 0) + 1
        assert request.query["username"] == "dXNlcituYW1lL3Rlc3Q9"
        assert request.query["password"] == password_digest("secret")
        return web.json_response(
            {
                "result": {
                    "session": {
                        "id": "session-one",
                        "expires": "2099-01-01T00:00:00+00:00",
                    },
                    "user": {"id": 987654},
                }
            }
        )

    async def refresh(request: web.Request) -> web.Response:
        calls["refresh"] = calls.get("refresh", 0) + 1
        form = await request.post()
        assert "client" in form
        assert request.headers["X-Session"] == "session-one"
        return web.json_response(
            {
                "result": {
                    "session": {
                        "id": "session-two",
                        "expiresIn": 3600,
                    }
                }
            }
        )

    async def devices(request: web.Request) -> web.Response:
        calls["devices"] = calls.get("devices", 0) + 1
        assert request.headers["X-Session"] in {"session-one", "session-two"}
        return web.json_response(
            {
                "result": [
                    {
                        "id": 453354,
                        "name": "Feeder",
                        "deviceType": "D4sh",
                        "modelCode": 2,
                        "p2pType": 2,
                        "firmware": "916",
                    }
                ]
            }
        )

    async def feed(request: web.Request) -> web.Response:
        calls["feed"] = calls.get("feed", 0) + 1
        form = await request.post()
        assert form["day"] == "20261020"
        assert form["time"] == "-1"
        assert form["amount1"] == "1"
        assert form["amount2"] == "0"
        if calls["feed"] == 1:
            return web.json_response(
                {"error": {"code": 6, "msg": "Session expired"}}
            )
        return web.json_response({"result": {"status": 1}})

    async def camera(request: web.Request) -> web.Response:
        assert request.query["definition"] == "2"
        return web.json_response(
            {
                "result": {
                    "channelId": "camera-channel",
                    "rtcToken": "rtc-token",
                    "rtmToken": "rtm-token",
                    "appRtmUserId": "app-user",
                    "devRtmUserId": "device-user",
                    "mediaEncryption": {
                        "mode": "AES_256_GCM2",
                        "key": "encryption-key",
                        "salt": "c2FsdA==",
                    },
                }
            }
        )

    async def records(request: web.Request) -> web.Response:
        assert request.query["deviceId"] == "453354"
        assert request.query["days"] == "20261020"
        return web.json_response(
            {
                "result": {
                    "eat": [
                        {
                            "items": [
                                {
                                    "eventId": "event-one",
                                    "petId": "2697855",
                                    "petName": "二狗",
                                }
                            ]
                        }
                    ]
                }
            }
        )

    app = web.Application()
    app.router.add_post("/6/user/login", login)
    app.router.add_post("/6/user/refreshsession", refresh)
    app.router.add_post("/6/d4sh/owndevices", devices)
    app.router.add_post("/6/d4sh/saveDailyFeed", feed)
    app.router.add_get("/6/d4sh/start/live", camera)
    app.router.add_post("/6/d4sh/getDeviceRecord", records)
    server = TestServer(app)
    await server.start_server()
    return server, calls


def test_device_login_and_refresh(monkeypatch) -> None:
    async def run() -> None:
        server, calls = await make_api_server()
        async with ClientSession() as session:
            client = PetkitClient(
                session,
                account="user+name/test=",
                password_hash=password_digest("secret"),
            )
            monkeypatch.setattr(
                "custom_components.petkit.client.BASE_URL",
                str(server.make_url("/6/")),
            )
            await client.async_login()
            assert client.user_id == "987654"
            devices = await client.async_discover_devices()
            assert devices[0].device_id == "453354"
            client._session_expires_at = 0
            devices = await client.async_discover_devices()
            assert devices[0].name == "Feeder"
            records = await client.async_fetch_device_record(
                "453354", day="20261020"
            )
            assert records["eat"][0]["items"][0]["petName"] == "二狗"
            assert calls["login"] == 1
            assert calls["refresh"] == 1
            assert calls["devices"] == 2
        await server.close()

    asyncio.run(run())


def test_feed_auth_error_is_not_retried(monkeypatch) -> None:
    async def run() -> None:
        server, calls = await make_api_server()
        async with ClientSession() as session:
            client = PetkitClient(
                session,
                account="user+name/test=",
                password_hash=password_digest("secret"),
            )
            monkeypatch.setattr(
                "custom_components.petkit.client.BASE_URL",
                str(server.make_url("/6/")),
            )
            await client.async_login()
            with pytest.raises(PetkitAuthError):
                await client.async_feed_now(
                    "453354", 1, 0, day="20261020"
                )
            assert calls["feed"] == 1
            camera = await client.async_get_camera_access("453354", 2)
            assert camera["app_id"] == "244c49951296440cbc1e3b937bf5e410"
            assert camera["rtc_uid"] == "987654"
        await server.close()

    asyncio.run(run())

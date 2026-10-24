"""Media sidecar client tests without a running camera."""

from __future__ import annotations

import asyncio

import pytest
from aiohttp import ClientSession, web
from aiohttp.test_utils import TestServer

from custom_components.petkit.media_client import PetkitMediaSidecarClient

pytestmark = [
    pytest.mark.enable_socket,
    pytest.mark.usefixtures("socket_enabled"),
]


async def _server() -> tuple[TestServer, list[tuple[str, str]]]:
    requests: list[tuple[str, str]] = []

    async def health(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        assert request.headers["Authorization"] == "Bearer sidecar-token"
        return web.json_response({"ok": True, "active_sessions": 0})

    async def create(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        payload = await request.json()
        assert payload["channel_id"] == "camera-channel"
        return web.json_response(
            {"session_id": "session-one", "expires_in": 900},
            status=201,
        )

    async def delete(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        return web.Response(status=204)

    async def stream(request: web.Request) -> web.Response:
        requests.append((request.method, request.path))
        return web.Response(
            body=b"#EXTM3U\n",
            headers={"Content-Type": "application/vnd.apple.mpegurl"},
        )

    app = web.Application()
    app.router.add_get("/healthz", health)
    app.router.add_post("/v1/sessions", create)
    app.router.add_delete("/v1/sessions/session-one", delete)
    app.router.add_get("/v1/sessions/session-one/video.m3u8", stream)
    server = TestServer(app)
    await server.start_server()
    return server, requests


def test_sidecar_client_lifecycle() -> None:
    async def run() -> None:
        server, requests = await _server()
        async with ClientSession() as session:
            client = PetkitMediaSidecarClient(
                session,
                base_url=str(server.make_url("/")),
                token="sidecar-token",
            )
            assert (await client.async_health())["ok"] is True
            created = await client.async_create_session(
                {
                    "app_id": "app",
                    "channel_id": "camera-channel",
                    "rtc_token": "rtc-token",
                    "rtc_uid": "123",
                    "media_encryption": {"key": "key", "salt": "c2FsdA=="},
                }
            )
            assert created["session_id"] == "session-one"
            response = await client.async_open_stream("session-one", "video.m3u8")
            assert response.status == 200
            assert await response.text() == "#EXTM3U\n"
            response.release()
            await client.async_delete_session("session-one")
        await server.close()
        assert requests == [
            ("GET", "/healthz"),
            ("POST", "/v1/sessions"),
            ("GET", "/v1/sessions/session-one/video.m3u8"),
            ("DELETE", "/v1/sessions/session-one"),
        ]

    asyncio.run(run())

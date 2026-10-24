"""Authenticated Home Assistant proxy for sidecar HLS resources."""

from __future__ import annotations

from typing import Any

from aiohttp import ClientError, web
from homeassistant.components.http import HomeAssistantView
from homeassistant.core import HomeAssistant

from .const import DOMAIN
from .media_client import PetkitMediaError, PetkitMediaSidecarClient

MEDIA_SESSIONS_KEY = "media_sessions"


def async_register_media_session(
    hass: HomeAssistant,
    session_id: str,
    client: PetkitMediaSidecarClient,
) -> None:
    """Remember which sidecar owns one opaque session."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    sessions = domain_data.setdefault(MEDIA_SESSIONS_KEY, {})
    sessions[session_id] = client


def async_remove_media_session(hass: HomeAssistant, session_id: str) -> None:
    """Remove one local sidecar session mapping."""
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    sessions = domain_data.get(MEDIA_SESSIONS_KEY, {})
    sessions.pop(session_id, None)


def _media_client(
    hass: HomeAssistant, session_id: str
) -> PetkitMediaSidecarClient | None:
    domain_data: dict[str, Any] = hass.data.get(DOMAIN, {})
    sessions = domain_data.get(MEDIA_SESSIONS_KEY, {})
    return sessions.get(session_id)


class PetkitMediaProxyView(HomeAssistantView):
    """Proxy sidecar HLS files behind normal Home Assistant authentication."""

    url = "/api/petkit/media/{session_id}/{filename}"
    name = "api:petkit:media"
    requires_auth = True

    def __init__(self, hass: HomeAssistant) -> None:
        self._hass = hass

    async def get(
        self, request: web.Request, session_id: str, filename: str
    ) -> web.StreamResponse:
        """Stream one allow-listed HLS resource."""
        video_segment = (
            filename.startswith("video-")
            and filename.endswith(".ts")
            and filename[6:-3].isdigit()
        )
        audio_segment = (
            filename.startswith("audio-")
            and filename.endswith(".m4s")
            and filename[6:-4].isdigit()
        )
        if "/" in filename or "\\" in filename or (
            filename not in {"video.m3u8", "audio.m3u8", "audio-init.mp4"}
            and not video_segment
            and not audio_segment
        ):
            return web.Response(status=404)

        client = _media_client(self._hass, session_id)
        if client is None:
            return web.Response(status=404)

        response = None
        try:
            response = await client.async_open_stream(session_id, filename)
            if response.status != 200:
                return web.Response(status=response.status)

            proxy = web.StreamResponse(
                status=200,
                headers={
                    "Content-Type": response.headers.get(
                        "Content-Type",
                        (
                            "application/vnd.apple.mpegurl"
                            if filename.endswith(".m3u8")
                            else "video/mp2t"
                            if filename.endswith(".ts")
                            else "video/mp4"
                        ),
                    ),
                    "Cache-Control": (
                        "no-store"
                        if filename.endswith(".m3u8")
                        else "private, max-age=60"
                    ),
                },
            )
            content_length = response.headers.get("Content-Length")
            if content_length and content_length.isdigit():
                proxy.content_length = int(content_length)
            await proxy.prepare(request)
            async for chunk in response.content.iter_chunked(64 * 1024):
                await proxy.write(chunk)
            await proxy.write_eof()
            return proxy
        except (ClientError, PetkitMediaError):
            if response is None:
                return web.Response(status=502)
            raise
        finally:
            if response is not None:
                response.release()

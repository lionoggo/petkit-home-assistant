"""Client for the optional native PETKIT media sidecar."""

from __future__ import annotations

from typing import Any
from urllib.parse import urljoin, urlsplit

from aiohttp import ClientError, ClientResponse, ClientSession, ClientTimeout


class PetkitMediaError(Exception):
    """The media sidecar could not complete a request."""


class PetkitMediaSidecarClient:
    """Call one authenticated PETKIT media sidecar."""

    def __init__(
        self,
        session: ClientSession,
        *,
        base_url: str,
        token: str,
    ) -> None:
        parsed = urlsplit(base_url)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("sidecar URL must be an absolute HTTP URL")
        if not token:
            raise ValueError("sidecar token is required")
        self._session = session
        self._base_url = base_url.rstrip("/") + "/"
        self._headers = {"Authorization": f"Bearer {token}"}
        self._timeout = ClientTimeout(total=25)

    async def async_health(self) -> dict[str, Any]:
        """Return sidecar health after checking its authenticated API."""
        try:
            async with self._session.get(
                urljoin(self._base_url, "healthz"),
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                if response.status != 200:
                    raise PetkitMediaError(
                        f"sidecar health failed with HTTP {response.status}"
                    )
                result = await response.json()
        except (ClientError, TimeoutError, ValueError) as err:
            raise PetkitMediaError("unable to reach the media sidecar") from err
        if not isinstance(result, dict) or result.get("ok") is not True:
            raise PetkitMediaError("media sidecar returned an invalid health response")
        return result

    async def async_create_session(
        self, camera_access: dict[str, Any]
    ) -> dict[str, Any]:
        """Create a short-lived native RTC session."""
        try:
            async with self._session.post(
                urljoin(self._base_url, "v1/sessions"),
                json=camera_access,
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                if response.status != 201:
                    raise PetkitMediaError(
                        f"sidecar session start failed with HTTP {response.status}"
                    )
                result = await response.json()
        except (ClientError, TimeoutError, ValueError) as err:
            raise PetkitMediaError("unable to start the media sidecar session") from err
        if (
            not isinstance(result, dict)
            or not isinstance(result.get("session_id"), str)
            or not result["session_id"]
        ):
            raise PetkitMediaError("media sidecar returned an invalid session")
        return result

    async def async_delete_session(self, session_id: str) -> None:
        """Stop a sidecar session; an already expired session is harmless."""
        try:
            async with self._session.delete(
                urljoin(
                    self._base_url,
                    f"v1/sessions/{_safe_path_component(session_id)}",
                ),
                headers=self._headers,
                timeout=self._timeout,
            ) as response:
                if response.status not in {200, 204, 404}:
                    raise PetkitMediaError(
                        f"sidecar session stop failed with HTTP {response.status}"
                    )
        except (ClientError, TimeoutError) as err:
            raise PetkitMediaError("unable to stop the media sidecar session") from err

    async def async_open_stream(
        self, session_id: str, filename: str
    ) -> ClientResponse:
        """Open one HLS resource for the Home Assistant authenticated proxy."""
        try:
            return await self._session.get(
                urljoin(
                    self._base_url,
                    (
                        f"v1/sessions/{_safe_path_component(session_id)}/"
                        f"{_safe_path_component(filename)}"
                    ),
                ),
                headers=self._headers,
                timeout=ClientTimeout(total=30),
            )
        except (ClientError, TimeoutError) as err:
            raise PetkitMediaError("unable to open the media stream") from err


def _safe_path_component(value: str) -> str:
    if not value or "/" in value or "\\" in value or value in {".", ".."}:
        raise PetkitMediaError("invalid media path")
    return value

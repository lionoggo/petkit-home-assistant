# PETKIT D4sh media sidecar

The sidecar joins the short-lived PETKIT Agora RTC session with the native
Agora Server SDK and exposes the camera as two HLS renditions:

- `video.m3u8`: H.264 video with normalized timestamps.
- `audio.m3u8`: 16 kHz mono AAC-LC with normalized timestamps.

The sidecar re-encodes both streams because Agora access units do not carry
timestamps that are portable across FFmpeg versions. The browser retains the
existing short-lived PETKIT RTM `start_live` and heartbeat responsibility in
this phase.

Home Assistant starts and stops sessions through an authenticated HTTP API and
proxies every HLS resource through its own HTTP authentication.

## Run

```bash
export PETKIT_MEDIA_TOKEN="$(openssl rand -hex 32)"
docker compose up -d --build
```

Configure the same URL and token under the PETKIT integration's **Configure**
action in Home Assistant.

## API

- `GET /healthz`
- `POST /v1/sessions`
- `GET /v1/sessions/{id}/status`
- `DELETE /v1/sessions/{id}`
- `GET /v1/sessions/{id}/video.m3u8`
- `GET /v1/sessions/{id}/audio.m3u8`

All routes require `Authorization: Bearer <PETKIT_MEDIA_TOKEN>`.

## Local build

```bash
./scripts/build-local.sh
```

The build script fetches the pinned `v2.4.22` Agora Go SDK and installs its
native runtime under `.build/`, which is excluded from Git.

The vendor-provided x86-64 Agora runtime uses AVX instructions. The HAOS App
detects x86-64 hosts without AVX and automatically runs the runtime through
QEMU. The standalone Docker image does not install that compatibility layer,
so x86-64 hosts must support AVX unless QEMU is provided separately.

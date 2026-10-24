ARG BUILD_FROM=ghcr.io/home-assistant/base-debian:bookworm

FROM golang:1.23-bookworm AS build

ARG AGORA_GO_SDK_TAG=v2.4.22

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl git unzip \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
RUN git clone --depth 1 --branch "${AGORA_GO_SDK_TAG}" \
    https://github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK.git agora-go-sdk

WORKDIR /src/agora-go-sdk
RUN ./scripts/install_agora_sdk.sh
COPY media_sidecar/ /src/agora-go-sdk/petkit_media/
RUN CGO_ENABLED=1 go build -trimpath -ldflags="-s -w" \
    -o /out/petkit-media ./petkit_media/cmd/petkit-media

FROM ${BUILD_FROM}

ARG BUILD_ARCH=amd64
ARG BUILD_VERSION=dev

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        qemu-user-static \
        libasound2 \
        libglib2.0-0 \
        libx11-6 \
        libxext6 \
        libxfixes3 \
    && groupadd --system --gid 65532 petkit \
    && useradd --uid 65532 --gid petkit \
        --no-create-home --home-dir /nonexistent \
        --shell /usr/sbin/nologin petkit \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/agora-go-sdk/agora_sdk /opt/agora
COPY --from=build /out/petkit-media /usr/local/bin/petkit-media
COPY petkit_media_app/rootfs /

ENV LD_LIBRARY_PATH=/opt/agora

LABEL \
    io.hass.type="app" \
    io.hass.version="${BUILD_VERSION}" \
    io.hass.arch="${BUILD_ARCH}" \
    org.opencontainers.image.title="PETKIT Media Bridge" \
    org.opencontainers.image.description="Native PETKIT D4sh media bridge for Home Assistant" \
    org.opencontainers.image.source="https://github.com/lionoggo/petkit-home-assistant" \
    org.opencontainers.image.licenses="MIT"

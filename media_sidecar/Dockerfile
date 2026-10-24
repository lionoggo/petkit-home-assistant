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

FROM debian:bookworm-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        ffmpeg \
        libasound2 \
        libglib2.0-0 \
        libx11-6 \
        libxext6 \
        libxfixes3 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=build /src/agora-go-sdk/agora_sdk /opt/agora
COPY --from=build /out/petkit-media /usr/local/bin/petkit-media

ENV LD_LIBRARY_PATH=/opt/agora
EXPOSE 8099
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/petkit-media"]

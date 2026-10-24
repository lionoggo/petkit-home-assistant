#!/bin/sh
set -eu

sidecar_root=$(CDPATH= cd -- "$(dirname "$0")/.." && pwd)
build_root="$sidecar_root/.build"
sdk_root="$build_root/agora-go-sdk"
sdk_tag=${AGORA_GO_SDK_TAG:-v2.4.22}

mkdir -p "$build_root"
if [ ! -d "$sdk_root/.git" ]; then
    git clone --depth 1 --branch "$sdk_tag" \
        https://github.com/AgoraIO-Extensions/Agora-Golang-Server-SDK.git \
        "$sdk_root"
else
    current_tag=$(git -C "$sdk_root" describe --tags --exact-match HEAD 2>/dev/null || true)
    if [ "$current_tag" != "$sdk_tag" ]; then
        git -C "$sdk_root" fetch --depth 1 origin "refs/tags/$sdk_tag"
        git -C "$sdk_root" checkout --detach FETCH_HEAD
    fi
fi

(
    cd "$sdk_root"
    ./scripts/install_agora_sdk.sh
)

mkdir -p "$sdk_root/petkit_media"
cp -R "$sidecar_root/cmd" "$sidecar_root/internal" "$sdk_root/petkit_media/"
(
    cd "$sdk_root"
    CGO_ENABLED=1 go build -o "$build_root/petkit-media" \
        ./petkit_media/cmd/petkit-media
)

printf 'Built %s\n' "$build_root/petkit-media"

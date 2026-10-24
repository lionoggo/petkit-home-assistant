# PETKIT Media Bridge

This app supplies the native Agora media sidecar required for PETKIT D4sh live
video and one-way audio. It does not store PETKIT account credentials and has
no access to the Home Assistant or Supervisor APIs.

## Installation

1. Install the **PETKIT D4sh Feeder** integration through HACS.
2. Add this repository as an app repository using **Settings -> Apps -> Install
   app -> Repositories**.
3. Install and start **PETKIT Media Bridge**.
4. Add the PETKIT integration account if it is not configured already.

When Home Assistant already has a PETKIT config entry, the app discovery
updates its media sidecar URL and token automatically and reloads the entry.
If no PETKIT account exists yet, discovery is kept as pending and applied when
the account is added.

The app listens on port `8099` inside the private app network. It does not
publish that port on the host and does not require `host_network`.

## Security

- A random 256-bit token is generated once in `/data/petkit_media_token`.
- The sidecar runs as UID/GID `65532` without Linux capabilities.
- The app does not request `homeassistant_api`, `hassio_api`, hardware access,
  or elevated Docker privileges.
- Browser clients never receive the sidecar token; media is proxied through the
  authenticated Home Assistant integration endpoint.

## Troubleshooting

Check the app log for `PETKIT media sidecar discovery sent`. If Home Assistant
does not receive discovery, open the PETKIT integration options and enter the
app URL and token manually. The URL uses the app hostname shown by the
Supervisor, normally `http://<app-hostname>:8099`.

## Maintainer release

The image is published by `.github/workflows/build-petkit-media-app.yml`.
Bump `version` in `config.yaml` before publishing a new image. After the first
successful GitHub Container Registry push, make the `petkit-ha-media` package
public so Home Assistant installations can pull it.

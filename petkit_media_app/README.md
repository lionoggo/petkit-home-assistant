# PETKIT Media Bridge

[![Open your Home Assistant instance and add the PETKIT app repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flionoggo%2Fpetkit-home-assistant)

Home Assistant App/Add-on for the native PETKIT D4sh live video and one-way
audio pipeline used by the PETKIT HACS integration.

The app runs an authenticated local sidecar and sends Home Assistant a
Supervisor discovery message with its internal URL and a generated token. The
integration stores those values automatically and proxies media through the
normal Home Assistant authentication layer.

Supported architectures: `amd64` and `aarch64`.

# Changelog

## 0.5.0

- Add an automatic QEMU compatibility path for x86-64 hosts without AVX.
- Keep native execution on ARM64 and x86-64 hosts that support AVX.

## 0.4.0

- Package the PETKIT media sidecar as a native Home Assistant App/Add-on.
- Add Supervisor discovery and automatic integration configuration.
- Publish `amd64` and `aarch64` images.
- Run the media process as an unprivileged user.

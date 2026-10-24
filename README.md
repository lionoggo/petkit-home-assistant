# PETKIT D4sh Feeder for Home Assistant

Unofficial Home Assistant integration for the PETKIT `D4sh` dual-bin smart
feeder (`com.petkit.android`). The integration uses the same private cloud APIs
as the official PETKIT app.

> This is a community integration, not a PETKIT product. PETKIT app or cloud
> changes can break authentication, controls, or video at any time.

## Features

- Account login, session refresh, and Home Assistant reauthentication.
- Automatic discovery of all PETKIT D4sh feeders on one account.
- Online, food-bin, feeding, eating, low-battery, error, plan, next-feed,
  desiccant, and daily statistics entities.
- Identified eating-pet sensors and a deduplicated pet-activity event entity.
- One-portion buttons for each bin and a general `petkit.feed_now` service.
- Complete seven-day schedule editor with pause/resume, copy-day, optimistic
  concurrency checks, and cloud save.
- On-demand live video and audio through a native media sidecar.
- Redacted diagnostics and no automatic retries for actions that dispense food.

## Delivery scope

Phase 1 covers account login, session refresh and reauthentication, D4sh
discovery, 30-second state polling, entities, immediate feeding, read-only
seven-day plans, and redacted diagnostics.

Phase 2 adds the optimistic seven-day schedule editor, immediate cloud plan
reload after conflicts, and on-demand live video and one-way audio through a
native Agora Server SDK sidecar.

PETKIT sends camera audio as AAC-LC under a custom Agora payload type. Browser
Agora SDKs do not reliably subscribe to that media, so the integration no
longer plays vendor audio directly in the browser. The sidecar receives the
encoded streams and exposes separate HLS video and audio renditions that the
card synchronizes and plays.

## Requirements

- Home Assistant `2026.6` or newer.
- A PETKIT account with a password configured.
- A D4sh feeder shared to that account.
- Internet access from Home Assistant to `api.petkit.cn` and Agora.
- The PETKIT Media Bridge HAOS App/Add-on, or Docker access for manual sidecar
  deployment. The image includes FFmpeg and the pinned Agora native SDK.

Use a dedicated account with the feeder shared to it. PETKIT sessions can
replace each other; using the owner account simultaneously with the phone app
can cause repeated sign-ins.

## Installation

### HACS custom repository

1. Add this repository as a HACS custom repository with category `Integration`.
2. Install **PETKIT D4sh Feeder**.
3. Restart Home Assistant.

### Manual

Copy `custom_components/petkit` into:

```text
<HA_CONFIG>/custom_components/petkit
```

Restart Home Assistant after copying the complete directory.

### HAOS App/Add-on

[![Open your Home Assistant instance and add the PETKIT app repository.](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2Flionoggo%2Fpetkit-home-assistant)

Add this repository as an app repository, then install and start **PETKIT Media
Bridge**:

1. Open **Settings → Apps → Install app → Repositories**.
2. Add `https://github.com/lionoggo/petkit-home-assistant`.
3. Install **PETKIT Media Bridge**.
4. Add the PETKIT integration account if it is not configured already.

The app supports `amd64` and `aarch64`. It uses the private app network, creates
its own token, and sends Supervisor discovery to this integration. When an
account entry exists, its sidecar URL and token are configured automatically.
On older x86-64 hosts without AVX, the app transparently runs the Agora native
SDK through QEMU user emulation. This keeps compatibility with low-power CPUs at
the cost of additional CPU overhead.

### Manual Docker sidecar

Build and start the sidecar from the repository:

```bash
export PETKIT_MEDIA_TOKEN="$(openssl rand -hex 32)"
cd media_sidecar
docker compose up -d --build
```

The sample binds the sidecar to `127.0.0.1:8099`. Use a private Docker network
or another protected network path when Home Assistant runs in a container.
The sidecar image supports the Linux architectures for which Agora publishes
the pinned native runtime; amd64 is the primary tested profile.

## Configuration

1. Open **Settings → Devices & services → Add integration**.
2. Search for **PETKIT D4sh Feeder**.
3. Enter the dedicated account and password.
4. If PETKIT requires SMS or graphical verification, finish the login in the
   official app, set an account password, and retry. The integration does not
   bypass PETKIT risk controls.
5. With the HAOS App/Add-on, sidecar discovery normally fills these fields
   automatically. For manual Docker deployment, open the integration entry's
   **Configure** action and set:
   - Sidecar URL, for example `http://petkit-media:8099`.
   - The same `PETKIT_MEDIA_TOKEN` provided to the sidecar.

All D4sh devices visible to the account are added automatically. If login later
expires, Home Assistant starts the reauthentication flow.

## Entities

Each D4sh device creates:

- Online
- Food bin 1 and food bin 2 (`empty`, `low`, `normal`, `unknown`)
- Desiccant remaining days
- Today feed, today eat, next feed
- Feed plan
- Feeding and eating
- Current and last identified eating pet
- Plan enabled today
- Low battery and problem
- Manual lock
- Feed bin 1 one portion and feed bin 2 one portion
- Refresh

The plan entity attributes include the full seven-day schedule, its stable
hash, the PETKIT device ID, and the device timezone.

## Immediate feeding

The two buttons each dispense one portion from their named bin. For a custom
amount, call:

```yaml
action: petkit.feed_now
data:
  device_id: "453354"
  amount1: 1
  amount2: 0
```

Each amount must be between 0 and 10, and at least one must be non-zero.
`device_id` is optional when exactly one D4sh device is loaded.

Feeding is a real physical action. The integration deliberately does not retry
this call after an ambiguous network or authentication failure.

## Identified eating pet

The integration polls `d4sh/getDeviceRecord` every 4 seconds to resolve the cat
identified by the feeder. This adds:

- `sensor.<device>_eating_pet`: the currently identified cat, or `none`.
- `sensor.<device>_last_eating_pet`: the most recently identified cat.
- `event.<device>_pet_activity`: `eating_started` and `eating_finished` events.

Identity data is available only when PETKIT's cloud record exposes the event.
The expected detection delay is 3-5 seconds plus cloud event generation time;
it is not local cat recognition. A D4sh that reports no matching pet ID uses
`none` or `unknown`.

## Schedule card

Add `/petkit_static/petkit-cards.js?v=0.5.0` as a JavaScript module resource,
then add:

```yaml
type: custom:petkit-schedule-card
entity: sensor.miao_de_fan_wan_feed_plan
title: PETKIT feeding schedule
```

The schedule card:

- edits all seven weekdays;
- adds and removes meals;
- sets each meal's time and two bin amounts;
- pauses individual weekdays;
- copies one day's meals to every other day;
- refuses to overwrite a schedule changed by another client.

The schedule is stored in the PETKIT cloud and is used by the feeder.

## Camera card

Add:

```yaml
type: custom:petkit-camera-card
device_id: "453354"
quality: hd
title: PETKIT live camera
```

Media starts only after pressing **Play**. Home Assistant requests a short-lived
PETKIT camera session and starts the native sidecar. The card plays the video
and audio HLS renditions behind Home Assistant authentication and stops the
session when:

- the card is removed;
- the browser tab remains hidden for 30 seconds;
- playback reaches 15 minutes;
- the user presses **Stop**.

The card supports video and one-way audio. It does not support two-way talk,
recording, native `camera.*`, NVR, or HomeKit.

## Services

- `petkit.feed_now`
- `petkit.save_schedule`
- `petkit.refresh_schedule`
- `petkit.get_camera_access`
- `petkit.start_camera_stream`
- `petkit.stop_camera_stream`

The supplied cards normally call these services. `get_camera_access` remains
available for diagnostics and older custom cards. See
`custom_components/petkit/services.yaml` for all fields.

## Security and privacy

- The integration stores the PETKIT password's MD5 value because that is the
  credential the vendor API requires. It does not store the plaintext password.
- Session IDs, RTC/RTM tokens, channel IDs, encryption keys, and media URLs are
  never written by integration logging or diagnostics.
- The sidecar token is stored in Home Assistant options, is never returned in
  diagnostics, and is not exposed to the browser.
- Camera credentials remain in browser memory only for the active playback
  session. The sidecar keeps short-lived camera credentials in memory only.
- All actions are cloud-dependent. No local/LAN control claim is made.

## Limitations

- Only the `D4sh` model is supported.
- MQTT push and PRIVATE PETKIT messaging are not used; state is polled every
  30 seconds.
- Audio and video are delivered as separate HLS renditions and synchronized in
  the browser. FFmpeg normalizes timestamps by re-encoding both streams, which
  preserves browser compatibility across macOS and Linux FFmpeg versions but
  consumes more CPU than remuxing.
- Some vendor status values are exposed as `unknown` until their semantics are
  confirmed.
- Identified eating is sourced from PETKIT cloud records; if the record appears
  only after eating finishes, `last_eating_pet` updates while `eating_pet`
  remains `none`.
- The integration does not manage device binding, sharing, firmware updates,
  recording, or notifications.
- HAOS App/Add-on requires `amd64` or `aarch64`; other architectures must use a
  compatible custom container deployment.

## Development checks

```bash
python3 -m venv /tmp/petkit-test-venv
/tmp/petkit-test-venv/bin/pip install -r requirements_test.txt
/tmp/petkit-test-venv/bin/pytest
node --check custom_components/petkit/frontend/petkit-cards.js
node --test tests/js/petkit_cards.test.mjs
./media_sidecar/scripts/build-local.sh
cd media_sidecar/.build/agora-go-sdk
go test ./petkit_media/...
```

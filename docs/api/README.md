# Atmoph Window Yo — BLE API Reference

**Version:** 1.0.0
**Updated:** 2026-07-09
**Contact:** <landi@athae.net>

A concise, self-contained reference to the BLE protocol of the Atmoph
Window Yo, sufficient for building a new integration from scratch without
needing to observe any traffic yourself.

Two machine-readable versions of this same document live alongside it:

- [`atmoph-api.json`](atmoph-api.json) — canonical, JSON Schema draft-2020-12 compatible
- [`atmoph-api.yaml`](atmoph-api.yaml) — same content, human-friendlier

If the JSON, YAML, and Markdown ever disagree, **the JSON is authoritative**.

## Methodology

This API was documented purely by **passive analysis of BLE traffic**
between the official Atmoph smartphone app and a device the user owns,
using Android's built-in HCI snoop log feature. No firmware or app was
decompiled; no authentication was bypassed (the device exposes its BLE
interface without authentication or pairing by design). See
[`../communication-analysis.md`](../communication-analysis.md) for the
full analysis process.

Fields marked **observed** are directly confirmed by capture. Fields
marked **inferred** are strong guesses that have not been round-tripped.

## Device discovery

The window advertises without pairing over BLE.

| Attribute | Value |
|---|---|
| Advertised name pattern | `Atmoph Window Yo <5-digit-serial>` |
| BLE MAC | **Rotates**. Never use as a permanent identifier |
| Pairing / bonding | Not required |
| Concurrent centrals | 1 (first-come-first-served) |

Discover the device by matching a substring of the advertised name —
typically the serial number, which is visible in the official app.

The official Atmoph app writes `"C"` (ASCII `0x43`) to the command
characteristic on connect. Third-party clients do not need to replicate
this; scripts in this repo work fine without it.

## Services

The device exposes two vendor-specific services plus the standard
Generic Attribute service. All useful characteristics live in a single
control service:

| Service | UUID |
|---|---|
| **Control** | `c1e0d952-12f7-4c84-b67d-fc26f55243a0` |

## Characteristics

### `command` — write commands

| Field | Value |
|---|---|
| UUID | `d4393824-471f-4799-ab74-28879878a4e7` |
| Properties | write |
| Encoding | ASCII |

General-purpose command register. Accepts variable-length ASCII strings.

| Command | Payload (ASCII) | Payload (hex) | Effect | Notes |
|---|---|---|---|---|
| Toggle power | `"S"` | `53` | Flip display power (sleep ↔ wake) | Not idempotent — read power state first |
| Next view | `"FW"` | `46 57` | Advance to next view | May require display awake |
| Previous view | `"BW"` | `42 57` | Go to previous view | May require display awake |
| App hello | `"C"` | `43` | Sent by official app on connect | Optional for third-party clients |

Other single-byte payloads (`"M"`, `"V"`, `"R"`, `"T"`, `"B"`) are used
by the official app to replay in-app menu navigation. Their standalone
effect has not been mapped.

### `power_state` — read/notify boolean

| Field | Value |
|---|---|
| UUID | `7607f5a4-22bc-4730-9019-c78dc8b50341` |
| Properties | read, write, notify |
| Encoding | ASCII string, values `"true"` or `"false"` |

Current display power state. Subscribe to notifications for realtime
updates when the user toggles power via the proximity sensor or physical
touch.

### `view_title` — read/notify UTF-8

| Field | Value |
|---|---|
| UUID | `1d862803-b301-4548-bece-1f1ab61881b8` |
| Properties | read, notify |
| Encoding | UTF-8 |
| Example | `"Evening in Plaza de la Paz"` |

Human-readable title of the currently displayed view. May contain
non-ASCII characters — always decode as UTF-8.

### `location` — read/notify UTF-8

| Field | Value |
|---|---|
| UUID | `275ddae2-4c69-4638-97d4-d5ba8e9e05d1` |
| Properties | read, notify |
| Encoding | UTF-8 |
| Example | `"Guanajuato"` |

Human-readable location / city name of the currently displayed view.

### `thumbnail_url` — read/notify URL

| Field | Value |
|---|---|
| UUID | `99cd2547-0640-485c-9996-e0a2b384a6f2` |
| Properties | read, notify |
| Encoding | UTF-8 |
| Example | `https://atmoph.com/thumbnails/LAT1_N1W2C3FL/fb7a2c47` |

HTTPS URL to the current view's thumbnail image on atmoph.com. Useful
for dashboards, image widgets, and Home Assistant sensor icons.

### `view_id_full` — read/notify machine identifier

| Field | Value |
|---|---|
| UUID | `03cffbfe-b23a-4c8f-bf57-9591b4d59119` |
| Properties | read, notify |
| Encoding | ASCII |
| Example | `LAT1_N1W2C3FL/fb7a2c47` |

Internal view identifier with hash suffix. Suitable as a stable
machine-readable "current view ID."

### `view_id_bare` — read/write/notify

| Field | Value |
|---|---|
| UUID | `e9c45eb5-fa81-4760-9b1b-24d6cb1d562c` |
| Properties | read, write, notify |
| Encoding | ASCII |
| Example | `LAT1_N1W2C3FL` |

Bare internal view identifier without hash suffix. The `write`
property suggests this may be used to select a specific view directly,
but this has **not been verified** — attempting a write may have no
effect, an unexpected effect, or work perfectly. If you test it, please
open a PR with your findings.

### `device_uuid` — stable identifier

| Field | Value |
|---|---|
| UUID | `e6f3269f-a0ce-49fa-9c46-8edbc02e0711` |
| Properties | read, write, notify |
| Encoding | ASCII UUID string |
| Example | `b2d1374e-6dfc-42e5-afeb-2e9a4331aa17` |

Stable device UUID. Unlike the BLE MAC, this does **not** rotate.
Suitable as a permanent identifier across firmware updates and reboot
cycles — for example, to disambiguate between multiple Window Yo
devices in the same home.

### `settings_json` — bulk settings

| Field | Value |
|---|---|
| UUID | `530bcd10-f723-4203-8222-0e135022d394` |
| Properties | read, write, notify |
| Encoding | UTF-8 JSON |

Aggregated device settings as a single JSON blob. Fields have
`{min, max, value}` triplets where meaningful. Writable but third-party
writes are **not verified**.

Example payload:

```json
{
  "WidgetsVisible": false,
  "DailyRoutineEnable": true,
  "LandscapeVolumeLevel":  {"min": 0, "max": 24, "value": 0},
  "SoundscapeLayer":       {"min": 0, "max": 5,  "value": 0},
  "SoundscapeVolumeLevel": {"min": 0, "max": 20, "value": 9},
  "ScreenBrightness":      {"min": 1, "max": 25, "value": 14},
  "CurrentDecoration":     {"min": 0, "max": 19, "value": 0},
  "SoundOnly": false,
  "LedBrightness":         {"min": 0, "max": 20, "value": 0}
}
```

### `lock_state` — child lock

| Field | Value |
|---|---|
| UUID | `596f4372-1456-4038-8bca-19ef89e6fe3e` |
| Properties | read, write |
| Encoding | UTF-8 JSON |
| Example | `{"IsLocked": false}` |

Child-lock state. Writable, unverified for third-party clients.

## Usage patterns

### Idempotent power on/off

The `power_toggle` command is a toggle, not an explicit on/off.
For repeatable automation:

```
1. Read `power_state`.
2. If already in the desired state, stop.
3. Otherwise, write "S" to `command`.
4. Optionally re-read `power_state` to confirm.
```

### Realtime state updates

Prefer notifications over polling. Open one connection, subscribe to
the four notify-capable state characteristics, and keep the connection
alive:

- `power_state`
- `view_title`
- `location`
- `thumbnail_url`

The device will emit a notification on each of these when the
corresponding value changes. This works both for changes initiated by
your client and for changes initiated externally (proximity sensor,
scheduled view rotation, another client).

### Waking before navigation

`next_view` and `prev_view` may require the display to be on. If
`power_state` is `"false"`, send `"S"` first, wait ~1 second for the
display to wake, then send the nav command.

## Constraints

- **Single central connection.** The device only accepts one BLE central
  at a time. Close the official app and any other client before
  connecting.
- **MAC rotation.** The BLE MAC is randomized. Do not cache it as a
  permanent identifier — always discover by advertised-name substring,
  or cache the MAC only as a scan-speed optimisation with a fall-through
  to a name scan.
- **UTF-8.** Some view titles and locations contain non-ASCII characters
  (e.g. Japanese place names). Always decode as UTF-8.

## Provenance

- **Project:** [atmoph-window-yo-ble](https://github.com/) (open source, MIT)
- **Contact:** <landi@athae.net>
- **Not affiliated with or endorsed by Atmoph Inc.**
- Protocol may change in any firmware update. Version this document
  when you observe changes.

## Change log

| Version | Date | Change |
|---|---|---|
| 1.0.0 | 2026-07-09 | Initial release with power, view/location/thumbnail state, next/previous view commands, and full characteristic map |

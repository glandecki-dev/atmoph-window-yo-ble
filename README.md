# Atmoph Window Yo — Local BLE Control

Local, cloud-free control of the [Atmoph Window Yo](https://atmoph.com/)
digital window over Bluetooth Low Energy. No app, no cloud, no internet.

The new Atmoph Window Yo no longer supports IFTTT or any other third-party
integration, and Atmoph does not publish a local API or document their BLE
protocol. This project is the result of analyzing the BLE communication
between the official app and the device, and provides Python scripts to
control the window and bridge it into home automation — making it the only
known way to integrate the Window Yo with home automation systems such as
Home Assistant or openHAB.

## Methodology — what was and wasn't done

This project did **not** involve any of the following:

- Decompiling, disassembling, or otherwise inspecting the official Atmoph
  app or the device firmware
- Modifying, patching, or tampering with any Atmoph software
- Bypassing any authentication, encryption, or access control (the device
  exposes its BLE interface without authentication or pairing by design)
- Exploiting any vulnerability

The protocol details documented here were obtained purely by **observing
the BLE traffic** between an Android phone running the official app and a
Window Yo device on the same local Bluetooth radio link, using Android's
standard built-in HCI snoop log feature. This is equivalent to looking at
network packets between two devices on your own network — passive
observation of communication on hardware the user owns.

## What works

- **Idempotent power on/off**, discovering the device by advertised name
  (handles BLE MAC rotation)
- **Cycle views**: next / previous view via BLE commands
- **Read current state**: power, view name, location, thumbnail URL
- **openHAB bridge daemon** with real-time BLE notifications, live power
  and view state, and bidirectional control (power switch and nav
  commands from openHAB)

## What doesn't

- Volume, brightness, decoration selection, calendar/clock toggles, etc.
  The same observation technique would reveal those, but they aren't
  mapped here. PRs welcome.
- Favorite state does not appear to be exposed as BLE state — it may be
  a cloud/app-only feature.

## Requirements

- Linux with BlueZ 5.40+
- Python 3.7+
- [`bleak`](https://github.com/hbldh/bleak) (`pip install -r requirements.txt`)
- An Atmoph Window Yo within Bluetooth range
- openHAB 3.0+ for the daemon (any release with the SSE `/rest/events`
  endpoint and REST API tokens)

## Scripts

| Script | Purpose |
|---|---|
| `atmoph_set.py` | One-shot idempotent on/off/status from the shell |
| `atmoph_view.py` | Cycle to next/previous view |
| `atmoph_get.py` | Read a single text value (view, location, ...) |
| `atmoph_read_all.py` | Dump every readable BLE characteristic (debug) |
| `atmoph_discover_state.py` | Discover state characteristics (debug) |
| `atmoph_openhab_daemon.py` | Persistent openHAB bridge (recommended) |

Before running any script, edit `NAME_FRAGMENT` near the top to a unique
substring of your window's advertised name (typically the 5-digit serial
number visible in the official app — e.g. `"Atmoph Window Yo 86637"` →
use `"86637"`).

## Usage — standalone

```bash
python3 atmoph_set.py on          # wake the window
python3 atmoph_set.py off         # sleep the window
python3 atmoph_set.py status      # print "on" or "off"

python3 atmoph_view.py next       # advance to next view
python3 atmoph_view.py prev       # go to previous view

python3 atmoph_get.py view        # print current view title
python3 atmoph_get.py location    # print current location
```

## Usage — openHAB daemon (recommended for home automation)

The daemon keeps a persistent BLE connection to the window and bridges it
to openHAB in both directions. State changes from the device (including
user actions on the physical screen) flow live to openHAB via BLE
notifications; commands from openHAB flow to the device via Server-Sent
Events on the openHAB REST API.

**Configure connection details** at the top of
`atmoph_openhab_daemon.py`:

```python
OPENHAB_URL = "http://openhab.local:8080"
OPENHAB_TOKEN = "oh.myuser.abcdef1234..."   # recommended
# OR:
OPENHAB_USER = "openhab"
OPENHAB_PASSWORD = "changeme"
```

**Create these openHAB items** (Settings → Items → Add Item):

| Item | Type | Notes |
|---|---|---|
| `AtmophPower` | Switch | Reflects display power, accepts `ON`/`OFF` commands |
| `AtmophView` | String | Current view title (read-only, updated by daemon) |
| `AtmophLocation` | String | Current location (read-only, updated by daemon) |
| `AtmophThumbnail` | String | Thumbnail URL (read-only, updated by daemon) |
| `AtmophNav` | String | Nav command sink; accepts `NEXT` or `PREV`. Set `autoupdate="false"` |

`AtmophNav` should have autoUpdate disabled so its state doesn't stick on
the last command:

```
String AtmophNav  "Nav command"  { autoupdate="false" }
```

Or via the UI: item → Add Metadata → Auto-update → `false`.

**Trigger navigation** from a rule, sitemap, or MainUI widget:

```javascript
// From a rule
sendCommand("AtmophNav", "NEXT")
sendCommand("AtmophNav", "PREV")
```

Sitemap:
```
Switch item=AtmophPower
Switch item=AtmophNav mappings=[NEXT="⏭ Next", PREV="⏮ Previous"]
Text   item=AtmophView
Text   item=AtmophLocation
```

## Running as a systemd service

Save as `/etc/systemd/system/atmoph-openhab.service` (adjust the `User`
and `ExecStart` path to match your install location):

```ini
[Unit]
Description=Atmoph Window Yo <-> openHAB bridge
After=network-online.target bluetooth.target
Wants=network-online.target bluetooth.target

[Service]
Type=simple
User=youruser
ExecStart=/usr/bin/python3 /path/to/atmoph-window-yo-ble/atmoph_openhab_daemon.py
Restart=always
RestartSec=15
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl daemon-reload
sudo systemctl enable --now atmoph-openhab
journalctl -u atmoph-openhab -f
```

## Protocol summary

| What | UUID | Payload |
|---|---|---|
| Service (control) | `c1e0d952-12f7-4c84-b67d-fc26f55243a0` | — |
| Command register (write) | `d4393824-471f-4799-ab74-28879878a4e7` | ASCII commands, see below |
| Power state (read / notify) | `7607f5a4-22bc-4730-9019-c78dc8b50341` | ASCII `"true"` / `"false"` |
| View title (read / notify) | `1d862803-b301-4548-bece-1f1ab61881b8` | UTF-8 text |
| Location (read / notify) | `275ddae2-4c69-4638-97d4-d5ba8e9e05d1` | UTF-8 text |
| Thumbnail URL (read / notify) | `99cd2547-0640-485c-9996-e0a2b384a6f2` | UTF-8 URL |

### Command register bytes

The command register accepts **variable-length ASCII strings** and acts as
a generic remote-control input:

| Command | Bytes | Action |
|---|---|---|
| `"S"` | `53` | Toggle power (sleep ↔ wake) |
| `"FW"` | `46 57` | Next view (Forward) |
| `"BW"` | `42 57` | Previous view (Backward) |

Other single-byte writes (`"C"`, `"M"`, `"V"`, `"R"`, `"T"`, `"B"`) are
used by the official app to replay in-app menu navigation. Their effects
outside the app UI are not fully mapped.

Power state is a pure toggle — the same `"S"` byte flips between on and
off. That's why the daemon and `atmoph_set.py` read the state
characteristic first and only write if a change is needed. See
[docs/communication-analysis.md](docs/communication-analysis.md) for the
full analysis process.

## How the protocol was identified

See [docs/communication-analysis.md](docs/communication-analysis.md) for
the full process. The technique is general — useful for any undocumented
BLE device controlled by a vendor smartphone app.

## Disclaimer

This project is not affiliated with or endorsed by Atmoph Inc. Use at your
own risk; the protocol could change in any firmware update.

## Contact

Landi — <landi@athae.net>

## License

MIT — see [LICENSE](LICENSE).

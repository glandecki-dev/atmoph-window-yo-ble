# Atmoph Window Yo — Local BLE Control

Local, cloud-free control of the [Atmoph Window Yo](https://atmoph.com/)
digital window over Bluetooth Low Energy. No app, no cloud, no internet, 
just enjoy quiet Window Yo when you are away.

The new Atmoph Window Yo no longer supports IFTTT or any other third-party
integration, and Atmoph does not publish a local API or document their BLE
protocol. This project is the result of analyzing the BLE communication
between the official app and the device, and provides a small Python script
to turn the window on/off idempotently — making it an easy way to integrate
the Window Yo with home automation systems such as Home Assistant, Openhab 
or any other (being able to run local python scripts).

To put it simple - I wanted to put 'the windows' to sleep while no one is
at home (in my case when the alarm is armed - covering anything from 
vacations to daily leave for work). 

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

- Discovers the window by advertised name (handles BLE MAC rotation)
  NOTE - you need to provide the unique part of your Window Yo advertisement
  (usually a number after "Atmoph Window Yo .." BLE name
- Reads current power state (`on` / `off`)
- Sets power state idempotently — only toggles if needed
- Suitable for Home Assistant `shell_command` or `command_line` switch

## What doesn't

- Volume, view selection, calendar/clock toggles, etc. The same observation
  technique would reveal those, but they aren't mapped here. PRs welcome.
  For me it was just enough to switch it off while we are away to save enery
  and money.

## Requirements

- An Atmoph Window Yo (of course) within Bluetooth range
- Linux with BlueZ 5.40+
- Python 3.7+
- [`bleak`](https://github.com/hbldh/bleak) (`pip install -r requirements.txt`)

## Usage

Edit `atmoph_set.py` and change `NAME_FRAGMENT` to a unique substring of your
window's advertised name (typically the 5-digit serial number visible in the
official app — e.g. `"Atmoph Window Yo 11223"` → use `"11223"`).

```bash
python3 atmoph_set.py on        # wake the window
python3 atmoph_set.py off       # sleep the window
python3 atmoph_set.py status    # print "on" or "off"
```

## Home Assistant integration

```yaml
shell_command:
  atmoph_on:     "/usr/bin/python3 /config/scripts/atmoph_set.py on"
  atmoph_off:    "/usr/bin/python3 /config/scripts/atmoph_set.py off"
  atmoph_status: "/usr/bin/python3 /config/scripts/atmoph_set.py status"
```

For a proper toggle entity with state feedback, wire it as a `command_line`
switch using the same three commands.

## Protocol summary

| What | UUID | Payload |
|---|---|---|
| Service (control) | `c1e0d952-12f7-4c84-b67d-fc26f55243a0` | — |
| Toggle power (write) | `d4393824-471f-4799-ab74-28879878a4e7` | `0x53` (ASCII `'S'`) |
| Power state (read / notify) | `7607f5a4-22bc-4730-9019-c78dc8b50341` | ASCII `"true"` / `"false"` |

The toggle byte `0x53` (`'S'`) flips the screen state. There is no separate
sleep/wake command — the same byte is interpreted relative to the current
state, which is why reading the state characteristic before writing matters
for idempotent control.

The window has roughly two dozen other vendor characteristics that almost
certainly expose volume, view selection, brightness, calendar and clock
visibility, etc. The same observation process documented in
[docs/communication-analysis.md](docs/communication-analysis.md) will
reveal them.

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

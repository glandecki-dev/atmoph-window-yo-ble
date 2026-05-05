# Analyzing the Atmoph Window Yo BLE communication

A clean, generalisable walkthrough for anyone wanting to apply the same
technique to another undocumented BLE device. The four phases are
**enumerate → capture → identify → replay**.

## Methodology and scope

This document describes how the Atmoph Window Yo's BLE protocol was
identified by **passively observing the BLE communication** between the
official Atmoph smartphone app and the device, on hardware the user owns.

What was **not** done at any point:

- No decompilation, disassembly, or static analysis of the official
  Atmoph app
- No inspection or modification of device firmware
- No bypassing of authentication or encryption — the device exposes its
  BLE interface without authentication or pairing by design
- No exploitation of any vulnerability

The technique is the BLE equivalent of running `tcpdump` on your own
network: the host operating system records the packets that pass through
its own radio, and a human looks at them. Android exposes this as a
built-in developer feature (the HCI snoop log).

## Background

The Atmoph Window Yo advertises over BLE and is controlled by Atmoph's
official smartphone app without pairing. Unlike the older Atmoph Window 2,
the new Window Yo no longer supports IFTTT or any other third-party
integration, and there is no public API or documentation of the BLE
protocol. Everything below was figured out from a single afternoon of
poking, with no special hardware — a Linux box with a built-in Bluetooth
adapter and an Android phone.

## Phase 1 — Enumerate the GATT tree

With the official app closed, scan and connect from Linux:

```bash
sudo apt install bluez bluez-tools
bluetoothctl
[bluetooth]# scan le
# wait for "Atmoph Window Yo NNNNN" to appear, note the MAC
[bluetooth]# scan off
[bluetooth]# connect AA:BB:CC:DD:EE:FF
[bluetooth]# menu gatt
[gatt]# list-attributes AA:BB:CC:DD:EE:FF
```

The Window exposes two vendor primary services (one ~14 characteristics,
one ~11) plus the standard Generic Attribute service. Every vendor
characteristic has a Client Characteristic Configuration descriptor
(`00002902`), meaning most of them support notifications. UUIDs are all
proprietary 128-bit values — no off-the-shelf service to lean on.

`bluetoothctl` doesn't show characteristic property flags. To see which are
writable vs notify-only, use `bleak`:

```python
import asyncio
from bleak import BleakClient
async def main():
    async with BleakClient("AA:BB:CC:DD:EE:FF") as c:
        for s in c.services:
            for ch in s.characteristics:
                print(ch.handle, ch.uuid, ch.properties)
asyncio.run(main())
```

(Skip `gatttool`. It's deprecated and fights `bluetoothd` over the
connection — symptoms look like a random "Disconnected" error.)

## Phase 2 — Observe the command from the official app

Don't try to sniff over the air with hardware. Android records every BLE
packet the phone exchanges, for free:

1. Settings → About phone → tap **Build number** 7 times to enable
   Developer Options.
2. Developer Options → enable **Bluetooth HCI snoop log**.
3. Toggle Bluetooth off, then on (the log starts fresh).
4. Open the Atmoph app, let it find the window, then perform the action
   you want to observe **once**. Don't do anything else — fewer packets
   makes analysis trivial.
5. Pull the log. The reliable way is `adb bugreport bugreport.zip`, then
   extract `FS/data/misc/bluetooth/logs/btsnoop_hci.log`. Some phones also
   put it at `/sdcard/btsnoop_hci.log` directly.

Open the log in Wireshark and filter:

```
btatt.opcode in {0x12, 0x52}
```

`0x12` is Write Request, `0x52` is Write Without Response. Atmoph's app
also writes `0x0100` to a swarm of CCCD descriptors at connection time
(that's how it subscribes to notifications) — you can subtract those by
adding `&& !(btatt.handle in {…})` with the descriptor handles from your
Phase 1 enumeration, but with only one user action observed the noise is
manageable anyway.

What you'll see for the sleep button: a single Write Request to a
specific handle, with a one-byte payload. Cross-reference the handle to
its UUID via Phase 1.

## Phase 3 — Identify, then replay

For the Window Yo, the observed write was:

| Field | Value |
|---|---|
| Service | `c1e0d952-12f7-4c84-b67d-fc26f55243a0` |
| Characteristic | `d4393824-471f-4799-ab74-28879878a4e7` |
| Payload | `0x53` (ASCII `'S'`) |

Replay it from Linux with [`bleak`](https://github.com/hbldh/bleak):

```python
import asyncio
from bleak import BleakClient
ADDR = "AA:BB:CC:DD:EE:FF"
CHAR = "d4393824-471f-4799-ab74-28879878a4e7"
async def main():
    async with BleakClient(ADDR) as c:
        await c.write_gatt_char(CHAR, bytes([0x53]), response=True)
asyncio.run(main())
```

Two practical notes:

**The MAC rotates.** BLE address randomisation means the address you
observed today is gone tomorrow. Discover by advertised-name fragment
instead — the production script in this repo does exactly that.

**The same byte does sleep and wake.** `0x53` is a *toggle*, not an
explicit on/off. That's fine for one-off scripts but bad for automation,
because state drifts every time someone uses the proximity sensor or the
official app. So:

## Phase 4 — Find the state characteristic

Many devices expose their current state on a notify-capable
characteristic, but you can't tell which one from the GATT tree alone —
and you don't need to guess. Subscribe to *every* notify characteristic
at once, fire the command twice with a gap in between, and look for the
one whose value flipped:

1. Connect.
2. For every characteristic with the `notify` property, call
   `start_notify` with a callback that records the payload.
3. Send the toggle command. Wait a couple of seconds.
4. Wait ten seconds in silence (helps separate state changes from any
   chatter the firmware emits unconditionally).
5. Send the toggle command again. Wait a couple of seconds.
6. Print, per characteristic, the values seen after each toggle.

Whichever characteristic shows two distinct values across the two phases
is your state register. `atmoph_discover_state.py` in this repo does
exactly this and prints a tidy report at the end.

For the Window Yo, the result was:

| Field | Value |
|---|---|
| Characteristic | `7607f5a4-22bc-4730-9019-c78dc8b50341` |
| Value when on | ASCII `"true"` |
| Value when off | ASCII `"false"` |

So you can read it any time without subscribing:

```python
raw = await client.read_gatt_char("7607f5a4-22bc-4730-9019-c78dc8b50341")
is_on = raw.decode("ascii").strip() == "true"
```

## Putting it together: idempotent control

With both pieces, you can build a real on/off:

1. Connect.
2. Read the state characteristic.
3. If it's already what you want, do nothing.
4. Otherwise, write the toggle byte.
5. Re-read state to confirm.

That's exactly what `atmoph_set.py` does, and it's what makes the script
safe to call from automations: send `off` ten times in a row and it'll
only actually toggle the first time.

## Generalising

This same recipe (enumerate → snoop log → Wireshark filter → replay with
bleak → discover state via mass notify subscription) works on most
unauthenticated BLE consumer devices that are app-controlled. The
specific UUIDs and payloads change; the workflow does not.

A few things worth knowing up front:

- `gatttool` is dead. Use `bleak` or `bluetoothctl`.
- A single observed command is rarely enough — observe the action, look
  at the surrounding writes, and treat any unfamiliar writes that
  consistently precede yours as part of an init handshake. (The Window Yo
  doesn't need a handshake. Many devices do.)
- "No pairing required" doesn't mean "no application-layer auth." Always
  worth verifying you can replay your observed packet from a fresh
  connection.
- Don't assume separate sleep/wake commands when there's a single toggle.
  Observe both UI actions to confirm.

## Contact

Landi — <landi@athae.net>

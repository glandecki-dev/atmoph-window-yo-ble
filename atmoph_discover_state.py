#!/usr/bin/env python3
"""
Atmoph Window Yo - state discovery helper.

Subscribes to every notify characteristic, sends the toggle command twice
(with a wait between), and prints which characteristics emitted values and
how those values changed between the two toggles. Whichever characteristic
flips between two distinct values is the state register.

Useful if you want to map characteristics other than power state, or if a
firmware update changes the UUIDs.

Contact: landi@athae.net
"""
import asyncio
import logging
import sys
from collections import defaultdict
from datetime import datetime

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# --- config ---------------------------------------------------------------
# Change NAME_FRAGMENT to your own Window Yo BLE advertisement
NAME_FRAGMENT = "11223"
TOGGLE_CHAR_UUID = "d4393824-471f-4799-ab74-28879878a4e7"
TOGGLE_PAYLOAD = bytes([0x53])  # ASCII 'S' - toggles power

SCAN_TIMEOUT = 15.0
CONNECT_TIMEOUT = 15.0
WAIT_BETWEEN_TOGGLES = 10.0
SETTLE_AFTER_TOGGLE = 2.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("atmoph")


# --- helpers --------------------------------------------------------------
def _hex(b):
    """bytes.hex(sep) wasn't available until Python 3.8."""
    return " ".join("{:02x}".format(x) for x in b)


def _describe(data):
    """Best-effort human description of a payload."""
    if len(data) == 0:
        return "empty"

    # Try ASCII first - Atmoph's protocol is ASCII-based.
    try:
        text = data.decode("ascii")
        if all(32 <= ord(c) < 127 for c in text):
            return "ascii={!r}".format(text)
    except UnicodeDecodeError:
        pass

    if len(data) == 1:
        return "u8={}".format(data[0])
    if len(data) <= 4:
        as_int_le = int.from_bytes(data, "little")
        return "hex={}, u{}_le={}".format(_hex(data), len(data) * 8, as_int_le)
    return "{} bytes, hex={}".format(len(data), _hex(data))


async def find_window(name_fragment, timeout):
    log.info("Scanning for device matching %r (up to %.0fs)...",
             name_fragment, timeout)
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and name_fragment in d.name:
            log.info("Found %r at %s", d.name, d.address)
            return d
    return None


# --- notification capture -------------------------------------------------
class NotificationRecorder:
    """Collects (timestamp, phase, bytes) per characteristic UUID."""

    def __init__(self):
        self.events = defaultdict(list)
        self._phase = "init"

    def set_phase(self, phase):
        self._phase = phase

    def make_callback(self, uuid):
        def _cb(_sender, data):
            payload = bytes(data)
            self.events[uuid].append((datetime.now(), self._phase, payload))
            log.info("  notify %s [%s] -> %s (%d bytes)",
                     uuid, self._phase, _hex(payload), len(payload))
        return _cb

    def report(self):
        if not self.events:
            log.warning("No notifications captured on any characteristic.")
            return

        print()
        print("=" * 78)
        print("STATE DISCOVERY REPORT")
        print("=" * 78)

        candidates = []
        for uuid, evts in self.events.items():
            after_first = [b for _, p, b in evts if p == "after_first_toggle"]
            after_second = [b for _, p, b in evts if p == "after_second_toggle"]
            print("\nCharacteristic {}".format(uuid))
            print("  after toggle #1 ({} notif): {}".format(
                len(after_first),
                [_hex(b) for b in after_first] or "<none>",
            ))
            print("  after toggle #2 ({} notif): {}".format(
                len(after_second),
                [_hex(b) for b in after_second] or "<none>",
            ))

            last_first = after_first[-1] if after_first else None
            last_second = after_second[-1] if after_second else None
            if (last_first is not None
                    and last_second is not None
                    and last_first != last_second):
                candidates.append((uuid, last_first, last_second))
                print("  >>> VALUE CHANGED between toggles "
                      "- likely state characteristic.")

        print()
        print("=" * 78)
        if candidates:
            print("Found {} characteristic(s) whose value flipped:".format(
                len(candidates)))
            for uuid, a, b in candidates:
                print("  {}".format(uuid))
                print("    state A: {}  ({})".format(_hex(a), _describe(a)))
                print("    state B: {}  ({})".format(_hex(b), _describe(b)))
            print("\nTo read state on demand without subscribing, try:")
            print("  await client.read_gatt_char({!r})".format(candidates[0][0]))
        else:
            print("No characteristic showed a clear state flip.")
            print("Possible reasons: state not exposed over BLE, or")
            print("notifications only fire on user action (try toggling via")
            print("the physical sensor or the official app while this script")
            print("runs).")
        print("=" * 78)


# --- main flow ------------------------------------------------------------
async def discover_state():
    device = await find_window(NAME_FRAGMENT, SCAN_TIMEOUT)
    if device is None:
        raise RuntimeError(
            "No BLE device with name containing {!r} found".format(NAME_FRAGMENT)
        )

    rec = NotificationRecorder()

    log.info("Connecting to %s ...", device.address)
    async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
        log.info("Connected. Subscribing to all notify characteristics...")

        subscribed = []
        for service in client.services:
            for char in service.characteristics:
                if "notify" in char.properties or "indicate" in char.properties:
                    try:
                        await client.start_notify(
                            char.uuid, rec.make_callback(char.uuid)
                        )
                        subscribed.append(char.uuid)
                    except Exception as e:
                        log.warning("Could not subscribe to %s: %s",
                                    char.uuid, e)

        log.info("Subscribed to %d characteristic(s).", len(subscribed))

        rec.set_phase("after_first_toggle")
        log.info("Sending toggle #1 (0x%02x)...", TOGGLE_PAYLOAD[0])
        await client.write_gatt_char(TOGGLE_CHAR_UUID, TOGGLE_PAYLOAD,
                                     response=True)
        log.info("Waiting %.0fs for notifications and state change...",
                 SETTLE_AFTER_TOGGLE)
        await asyncio.sleep(SETTLE_AFTER_TOGGLE)

        rec.set_phase("idle_between")
        idle_remainder = WAIT_BETWEEN_TOGGLES - SETTLE_AFTER_TOGGLE
        if idle_remainder > 0:
            log.info("Idle %.0fs before second toggle...", idle_remainder)
            await asyncio.sleep(idle_remainder)

        rec.set_phase("after_second_toggle")
        log.info("Sending toggle #2 (0x%02x)...", TOGGLE_PAYLOAD[0])
        await client.write_gatt_char(TOGGLE_CHAR_UUID, TOGGLE_PAYLOAD,
                                     response=True)
        log.info("Waiting %.0fs for notifications...", SETTLE_AFTER_TOGGLE)
        await asyncio.sleep(SETTLE_AFTER_TOGGLE)

        rec.set_phase("done")

        for uuid in subscribed:
            try:
                await client.stop_notify(uuid)
            except Exception:
                pass

    rec.report()


async def main():
    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await discover_state()
            return 0
        except (BleakError, RuntimeError, asyncio.TimeoutError) as e:
            last_exc = e
            log.warning("Attempt %d/%d failed: %s", attempt, MAX_ATTEMPTS, e)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF)
    log.error("All %d attempts failed. Last error: %s", MAX_ATTEMPTS, last_exc)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()))
    except KeyboardInterrupt:
        sys.exit(130)

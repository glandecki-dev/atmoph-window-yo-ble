#!/usr/bin/env python3
"""
Atmoph Window Yo - idempotent on/off over BLE.

Usage:
  atmoph_set.py on        # ensure the window is awake
  atmoph_set.py off       # ensure the window is asleep
  atmoph_set.py status    # print "on" or "off"

Reads the current power state before writing, so calling `off` ten times in
a row only sends one toggle. Discovers the device by advertised-name
fragment because the BLE MAC rotates.

Contact: landi@athae.net
"""
import asyncio
import logging
import sys

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# --- config ---------------------------------------------------------------
# Unique substring of the window's advertised name (typically the serial).
NAME_FRAGMENT = "86637"

# Vendor characteristic that toggles the screen power. Writing 0x53 ('S')
# flips state. Same byte for sleep and wake - read the state char first.
TOGGLE_CHAR = "d4393824-471f-4799-ab74-28879878a4e7"
TOGGLE_BYTE = bytes([0x53])

# Vendor characteristic exposing the current power state as ASCII
# "true" / "false".
STATE_CHAR = "7607f5a4-22bc-4730-9019-c78dc8b50341"

SCAN_TIMEOUT = 15.0
CONNECT_TIMEOUT = 15.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0
SETTLE_AFTER_TOGGLE = 1.5  # let firmware update state before re-reading

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
log = logging.getLogger("atmoph")


async def find_window(name_fragment, timeout):
    log.info("Scanning for %r ...", name_fragment)
    devices = await BleakScanner.discover(timeout=timeout)
    for d in devices:
        if d.name and name_fragment in d.name:
            log.info("Found %r at %s", d.name, d.address)
            return d
    return None


async def read_state(client):
    raw = bytes(await client.read_gatt_char(STATE_CHAR))
    text = raw.decode("ascii", errors="replace").strip()
    log.info("Current state: %r", text)
    if text == "true":
        return True
    if text == "false":
        return False
    raise RuntimeError("Unexpected state payload: {!r}".format(raw))


async def set_state(desired):
    """desired: True (on/awake), False (off/sleep), None for status-only."""
    device = await find_window(NAME_FRAGMENT, SCAN_TIMEOUT)
    if device is None:
        raise RuntimeError(
            "No device with name containing {!r}".format(NAME_FRAGMENT)
        )

    async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
        current = await read_state(client)

        if desired is None:
            print("on" if current else "off")
            return

        if current == desired:
            log.info("Already %s, nothing to do.", "on" if desired else "off")
            return

        log.info("Toggling to %s ...", "on" if desired else "off")
        await client.write_gatt_char(TOGGLE_CHAR, TOGGLE_BYTE, response=True)
        await asyncio.sleep(SETTLE_AFTER_TOGGLE)

        new = await read_state(client)
        if new != desired:
            raise RuntimeError(
                "State did not change as expected (now {})".format(
                    "on" if new else "off"
                )
            )
        log.info("Done.")


async def main(argv):
    if len(argv) != 2 or argv[1] not in ("on", "off", "status"):
        print("Usage: {} on|off|status".format(argv[0]))
        return 2

    desired = {"on": True, "off": False, "status": None}[argv[1]]

    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await set_state(desired)
            return 0
        except (BleakError, RuntimeError, asyncio.TimeoutError) as e:
            last_exc = e
            log.warning("Attempt %d/%d failed: %s",
                        attempt, MAX_ATTEMPTS, e)
            if attempt < MAX_ATTEMPTS:
                await asyncio.sleep(RETRY_BACKOFF)
    log.error("All attempts failed. Last error: %s", last_exc)
    return 1


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main(sys.argv)))
    except KeyboardInterrupt:
        sys.exit(130)

#!/usr/bin/env python3
"""
Atmoph Window Yo - cycle to next or previous view.

Usage:
  atmoph_view.py next     # advance to next view (Forward)
  atmoph_view.py prev     # go to previous view (Backward)

Discovers the device by advertised-name fragment because the BLE MAC
rotates. Uses the same command register as the power toggle
(d4393824-...) with different ASCII payloads.

Contact: landi@athae.net
"""
import asyncio
import logging
import sys

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

NAME_FRAGMENT = "86637"
CMD_CHAR = "d4393824-471f-4799-ab74-28879878a4e7"

COMMANDS = {
    "next": b"FW",   # Forward
    "prev": b"BW",   # Backward
}

SCAN_TIMEOUT = 15.0
CONNECT_TIMEOUT = 15.0
MAX_ATTEMPTS = 3
RETRY_BACKOFF = 2.0

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


async def send_nav(action):
    payload = COMMANDS[action]
    device = await find_window(NAME_FRAGMENT, SCAN_TIMEOUT)
    if device is None:
        raise RuntimeError(
            "No device with name containing {!r}".format(NAME_FRAGMENT)
        )

    log.info("Connecting to %s ...", device.address)
    async with BleakClient(device, timeout=CONNECT_TIMEOUT) as client:
        log.info("Sending %s command (%r)...", action, payload)
        await client.write_gatt_char(CMD_CHAR, payload, response=True)
        log.info("Done.")


async def main(argv):
    if len(argv) != 2 or argv[1] not in COMMANDS:
        print("Usage: {} {{{}}}".format(
            argv[0], "|".join(sorted(COMMANDS))
        ))
        return 2

    action = argv[1]

    last_exc = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            await send_nav(action)
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

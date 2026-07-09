#!/usr/bin/env python3
"""
Atmoph Window Yo <-> openHAB bidirectional daemon.

Maintains a persistent BLE connection to the window and bridges it to openHAB:

Device -> openHAB (via notifications):
  - Power state (Switch item)
  - Current view name (String item)
  - Current location (String item)
  - Current thumbnail URL (String item)

openHAB -> Device (via SSE command events):
  - Power switch ON/OFF   -> writes "S" to command register if state differs
  - Nav string NEXT/PREV  -> writes "FW" / "BW" to command register

Reconnects automatically after BLE drops or openHAB restarts.

Contact: landi@athae.net
"""
import asyncio
import base64
import json
import logging
import os
import signal
import ssl
import sys
import urllib.error
import urllib.request

from bleak import BleakClient, BleakScanner
from bleak.exc import BleakError

# ============================================================================
# CONFIGURATION
# ============================================================================

# --- BLE / Atmoph ---
NAME_FRAGMENT = "86637"

CHAR_STATE     = "7607f5a4-22bc-4730-9019-c78dc8b50341"  # true / false
CHAR_COMMAND   = "d4393824-471f-4799-ab74-28879878a4e7"  # multi-command register
CHAR_VIEW      = "1d862803-b301-4548-bece-1f1ab61881b8"
CHAR_LOCATION  = "275ddae2-4c69-4638-97d4-d5ba8e9e05d1"
CHAR_THUMBNAIL = "99cd2547-0640-485c-9996-e0a2b384a6f2"

# ASCII commands written to CHAR_COMMAND. Confirmed by BLE capture:
CMD_POWER_TOGGLE = b"S"    # flips power state (sleep <-> wake)
CMD_NEXT_VIEW    = b"FW"   # advance to next view (Forward)
CMD_PREV_VIEW    = b"BW"   # go to previous view (Backward)

# --- openHAB ---
OPENHAB_URL = "http://openhab.local:8080"

# Use EITHER an API token (recommended, openHAB 3+) OR user/password.
OPENHAB_TOKEN    = None                    # "oh.myuser.abcdef1234..."
OPENHAB_USER     = "openhab"
OPENHAB_PASSWORD = "changeme"
OPENHAB_VERIFY_TLS = True

# Item types:
#   ITEM_POWER      -> Switch  (ON / OFF)
#   ITEM_VIEW       -> String
#   ITEM_LOCATION   -> String
#   ITEM_THUMBNAIL  -> String
#   ITEM_NAV        -> String  (accepts commands "NEXT" or "PREV")
ITEM_POWER     = "AtmophPower"
ITEM_VIEW      = "AtmophView"
ITEM_LOCATION  = "AtmophLocation"
ITEM_THUMBNAIL = "AtmophThumbnail"
ITEM_NAV       = "AtmophNav"

# --- Behaviour ---
RECONNECT_DELAY     = 10.0
SSE_RECONNECT_DELAY = 5.0
FAST_SCAN_TIMEOUT   = 5.0
FULL_SCAN_TIMEOUT   = 15.0
CONNECT_TIMEOUT     = 15.0
HTTP_TIMEOUT        = 10.0

# ============================================================================
# INTERNAL
# ============================================================================

CACHE_DIR = os.path.join(
    os.environ.get("XDG_CACHE_HOME", os.path.expanduser("~/.cache")),
    "atmoph-window-yo",
)
CACHE_FILE = os.path.join(CACHE_DIR, "mac")

# Items whose /command events we care about, mapped to command kind.
_COMMAND_ITEMS = {
    ITEM_POWER: "power",
    ITEM_NAV:   "nav",
}

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    stream=sys.stderr,
)
log = logging.getLogger("atmoph")


# --- MAC cache ---
def load_cached_mac():
    try:
        with open(CACHE_FILE, "r") as f:
            mac = f.read().strip()
            return mac or None
    except (OSError, IOError):
        return None


def save_cached_mac(mac):
    try:
        os.makedirs(CACHE_DIR, exist_ok=True)
        with open(CACHE_FILE, "w") as f:
            f.write(mac + "\n")
    except (OSError, IOError) as e:
        log.warning("Could not write MAC cache: %s", e)


def clear_cached_mac():
    try:
        os.remove(CACHE_FILE)
    except (OSError, IOError):
        pass


# --- BLE discovery ---
async def acquire_device():
    cached = load_cached_mac()
    if cached:
        log.info("Looking for cached MAC %s...", cached)
        device = await BleakScanner.find_device_by_address(
            cached, timeout=FAST_SCAN_TIMEOUT
        )
        if device is not None:
            return device
        log.info("Cached MAC not seen, falling back to name scan.")

    log.info("Scanning for %r...", NAME_FRAGMENT)
    device = await BleakScanner.find_device_by_filter(
        lambda d, ad: bool(d.name and NAME_FRAGMENT in d.name),
        timeout=FULL_SCAN_TIMEOUT,
    )
    if device is None:
        if cached:
            clear_cached_mac()
        raise RuntimeError(
            "No device with name containing {!r}".format(NAME_FRAGMENT)
        )

    log.info("Found %r at %s", device.name, device.address)
    if device.address != cached:
        save_cached_mac(device.address)
    return device


# --- openHAB REST (blocking helpers, called from executor) ---
def openhab_auth_header():
    if OPENHAB_TOKEN:
        return "Bearer " + OPENHAB_TOKEN
    if OPENHAB_USER and OPENHAB_PASSWORD:
        creds = "{}:{}".format(OPENHAB_USER, OPENHAB_PASSWORD)
        return "Basic " + base64.b64encode(creds.encode()).decode()
    return None


def _tls_context():
    if OPENHAB_URL.lower().startswith("https://") and not OPENHAB_VERIFY_TLS:
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def openhab_update_item_blocking(item_name, value):
    url = "{}/rest/items/{}/state".format(OPENHAB_URL.rstrip("/"), item_name)
    headers = {"Content-Type": "text/plain", "Accept": "application/json"}
    auth = openhab_auth_header()
    if auth:
        headers["Authorization"] = auth
    req = urllib.request.Request(
        url, data=value.encode("utf-8"), headers=headers, method="PUT"
    )
    with urllib.request.urlopen(
        req, timeout=HTTP_TIMEOUT, context=_tls_context()
    ) as resp:
        if resp.status not in (200, 202):
            raise RuntimeError("openHAB HTTP {}".format(resp.status))


# ============================================================================
# BRIDGE
# ============================================================================
class Bridge:
    def __init__(self):
        self.command_queue = asyncio.Queue()  # of (kind, value)
        self.ble_lock = asyncio.Lock()
        self.client = None
        self.current_power = None  # True/False/None
        self.stop_event = asyncio.Event()

    # --- openHAB write ---
    async def push_item(self, item_name, value):
        loop = asyncio.get_running_loop()
        try:
            await loop.run_in_executor(
                None, openhab_update_item_blocking, item_name, value
            )
            log.info("openHAB <- %s = %r", item_name, value)
        except Exception as e:
            log.error("openHAB update failed for %s: %s", item_name, e)

    # --- openHAB SSE listener (persistent, survives BLE reconnects) ---
    async def sse_listener_forever(self):
        loop = asyncio.get_running_loop()
        while not self.stop_event.is_set():
            try:
                await loop.run_in_executor(None, self._sse_run_blocking, loop)
            except Exception as e:
                log.warning("SSE listener error: %s", e)
            if self.stop_event.is_set():
                break
            log.info("Reconnecting openHAB SSE in %.0fs...",
                     SSE_RECONNECT_DELAY)
            try:
                await asyncio.wait_for(
                    self.stop_event.wait(), timeout=SSE_RECONNECT_DELAY
                )
                break
            except asyncio.TimeoutError:
                pass

    def _sse_run_blocking(self, loop):
        # Subscribe to /command topics for every item we care about.
        topics = ",".join(
            "openhab/items/{}/command".format(name)
            for name in _COMMAND_ITEMS
        )
        url = "{}/rest/events?topics={}".format(
            OPENHAB_URL.rstrip("/"), topics
        )
        headers = {"Accept": "text/event-stream"}
        auth = openhab_auth_header()
        if auth:
            headers["Authorization"] = auth

        req = urllib.request.Request(url, headers=headers)
        log.info("openHAB SSE: connecting to %s", url)
        with urllib.request.urlopen(req, context=_tls_context()) as resp:
            log.info("openHAB SSE: listening for commands on %s",
                     ", ".join(_COMMAND_ITEMS))
            buffer_data = []
            for line_bytes in resp:
                line = line_bytes.decode("utf-8", "replace").rstrip("\r\n")
                if line == "":
                    if buffer_data:
                        self._handle_sse_data("\n".join(buffer_data), loop)
                    buffer_data = []
                elif line.startswith("data:"):
                    buffer_data.append(line[5:].lstrip())

    def _handle_sse_data(self, data_str, loop):
        try:
            outer = json.loads(data_str)
            if outer.get("type") != "ItemCommandEvent":
                return
            topic = outer.get("topic", "")
            # Topic format: "openhab/items/{name}/command"
            parts = topic.split("/")
            if len(parts) < 4 or parts[-1] != "command":
                return
            item_name = parts[-2]
            kind = _COMMAND_ITEMS.get(item_name)
            if kind is None:
                return

            payload = json.loads(outer.get("payload", "{}"))
            value = payload.get("value")
            log.info("openHAB -> %s command: %r", item_name, value)

            if kind == "power" and value in ("ON", "OFF"):
                asyncio.run_coroutine_threadsafe(
                    self.command_queue.put(("power", value)), loop
                )
            elif kind == "nav" and value in ("NEXT", "PREV"):
                asyncio.run_coroutine_threadsafe(
                    self.command_queue.put(("nav", value)), loop
                )
            else:
                log.warning("Ignoring unsupported %s command: %r",
                            item_name, value)
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            log.warning("Malformed SSE event: %s", e)

    # --- BLE notification handlers ---
    def _make_notify_cb(self, handler):
        def _cb(_sender, data):
            asyncio.create_task(handler(bytes(data)))
        return _cb

    async def handle_state_notify(self, data):
        text = data.decode("ascii", "replace").strip()
        if text not in ("true", "false"):
            log.warning("Unexpected state payload: %r", text)
            return
        is_on = text == "true"
        was_on = self.current_power
        self.current_power = is_on
        log.info("BLE -> state: %s", "ON" if is_on else "OFF")
        await self.push_item(ITEM_POWER, "ON" if is_on else "OFF")
        # On OFF->ON transition, force a content refresh in case the view
        # changed while the display was asleep.
        if is_on and was_on is False:
            log.info("Display just turned on; refreshing content fields.")
            asyncio.create_task(self.refresh_content_fields())

    async def handle_view_notify(self, data):
        text = data.decode("utf-8", "replace").strip()
        log.info("BLE -> view: %r", text)
        await self.push_item(ITEM_VIEW, text)

    async def handle_location_notify(self, data):
        text = data.decode("utf-8", "replace").strip()
        log.info("BLE -> location: %r", text)
        await self.push_item(ITEM_LOCATION, text)

    async def handle_thumbnail_notify(self, data):
        text = data.decode("utf-8", "replace").strip()
        log.info("BLE -> thumbnail: %r", text)
        await self.push_item(ITEM_THUMBNAIL, text)

    async def refresh_content_fields(self):
        if self.client is None:
            return
        try:
            async with self.ble_lock:
                for char, item in [
                    (CHAR_VIEW, ITEM_VIEW),
                    (CHAR_LOCATION, ITEM_LOCATION),
                    (CHAR_THUMBNAIL, ITEM_THUMBNAIL),
                ]:
                    raw = bytes(await self.client.read_gatt_char(char))
                    text = raw.decode("utf-8", "replace").strip()
                    await self.push_item(item, text)
        except Exception as e:
            log.error("Content refresh failed: %s", e)

    # --- Command execution ---
    async def apply_power(self, want_value):
        want_on = (want_value == "ON")
        async with self.ble_lock:
            raw = bytes(await self.client.read_gatt_char(CHAR_STATE))
            is_on = raw.decode("ascii", "replace").strip() == "true"
            self.current_power = is_on
            if is_on == want_on:
                log.info("Already %s; nothing to do.", want_value)
                await self.push_item(
                    ITEM_POWER, "ON" if is_on else "OFF"
                )
                return
            log.info("Toggling power: %s -> %s",
                     "ON" if is_on else "OFF", want_value)
            await self.client.write_gatt_char(
                CHAR_COMMAND, CMD_POWER_TOGGLE, response=True
            )
            # State notification will fire and update openHAB.

    async def apply_nav(self, direction):
        payload = CMD_NEXT_VIEW if direction == "NEXT" else CMD_PREV_VIEW
        log.info("Nav: %s (writing %r)", direction, payload)
        async with self.ble_lock:
            # If the display is off, waking it first mirrors what the app
            # does. Send a power toggle, wait briefly, then the nav.
            if self.current_power is False:
                log.info("Display is off; waking first.")
                await self.client.write_gatt_char(
                    CHAR_COMMAND, CMD_POWER_TOGGLE, response=True
                )
                await asyncio.sleep(1.0)
            await self.client.write_gatt_char(
                CHAR_COMMAND, payload, response=True
            )
        # The device will emit view/location/thumbnail notifications,
        # which the existing handlers push to openHAB automatically.

    async def process_commands(self):
        while True:
            kind, value = await self.command_queue.get()

            # For power commands, coalesce any queued power commands to the
            # latest one so a rapid ON/OFF/ON only fires once. Nav commands
            # in the queue are preserved and applied afterwards, in order.
            if kind == "power":
                pending_nav = []
                while not self.command_queue.empty():
                    try:
                        nxt = self.command_queue.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if nxt[0] == "power":
                        value = nxt[1]  # keep latest
                    else:
                        pending_nav.append(nxt)
                # Re-queue nav commands preserving order
                for n in pending_nav:
                    await self.command_queue.put(n)

            try:
                if kind == "power":
                    await self.apply_power(value)
                elif kind == "nav":
                    await self.apply_nav(value)
            except Exception as e:
                log.error("Failed to apply %s %s: %s", kind, value, e)

    # --- BLE session ---
    async def run_ble_session(self):
        device = await acquire_device()
        disconnected = asyncio.Event()

        def on_disconnect(_client):
            log.warning("BLE disconnected.")
            disconnected.set()

        log.info("Connecting to %s...", device.address)
        async with BleakClient(
            device,
            timeout=CONNECT_TIMEOUT,
            disconnected_callback=on_disconnect,
        ) as client:
            self.client = client
            log.info("BLE connected.")

            # Initial state sync
            raw = bytes(await client.read_gatt_char(CHAR_STATE))
            is_on = raw.decode("ascii", "replace").strip() == "true"
            self.current_power = is_on
            log.info("Initial state: %s", "ON" if is_on else "OFF")
            await self.push_item(ITEM_POWER, "ON" if is_on else "OFF")
            if is_on:
                await self.refresh_content_fields()

            # Subscribe to notifications
            await client.start_notify(
                CHAR_STATE,
                self._make_notify_cb(self.handle_state_notify),
            )
            await client.start_notify(
                CHAR_VIEW,
                self._make_notify_cb(self.handle_view_notify),
            )
            await client.start_notify(
                CHAR_LOCATION,
                self._make_notify_cb(self.handle_location_notify),
            )
            await client.start_notify(
                CHAR_THUMBNAIL,
                self._make_notify_cb(self.handle_thumbnail_notify),
            )
            log.info("Subscribed to state/view/location/thumbnail.")

            # Run command processor until disconnected or asked to stop
            processor = asyncio.create_task(self.process_commands())
            stopper = asyncio.create_task(self.stop_event.wait())
            dropper = asyncio.create_task(disconnected.wait())
            try:
                await asyncio.wait(
                    {stopper, dropper},
                    return_when=asyncio.FIRST_COMPLETED,
                )
            finally:
                for t in (processor, stopper, dropper):
                    t.cancel()
                self.client = None
                self.current_power = None


# ============================================================================
# ENTRYPOINT
# ============================================================================
async def main():
    bridge = Bridge()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, bridge.stop_event.set)
        except NotImplementedError:
            pass

    sse_task = asyncio.create_task(bridge.sse_listener_forever())

    log.info("Daemon started. Ctrl-C or SIGTERM to stop.")
    try:
        while not bridge.stop_event.is_set():
            try:
                await bridge.run_ble_session()
            except (BleakError, RuntimeError, asyncio.TimeoutError) as e:
                log.warning("BLE session ended: %s", e)
            except Exception:
                log.exception("Unexpected BLE session error")
            if bridge.stop_event.is_set():
                break
            log.info("Reconnecting to Atmoph in %.0fs...", RECONNECT_DELAY)
            try:
                await asyncio.wait_for(
                    bridge.stop_event.wait(), timeout=RECONNECT_DELAY
                )
                break
            except asyncio.TimeoutError:
                pass
    finally:
        log.info("Shutting down.")
        sse_task.cancel()
        try:
            await sse_task
        except (asyncio.CancelledError, Exception):
            pass


if __name__ == "__main__":
    try:
        sys.exit(asyncio.run(main()) or 0)
    except KeyboardInterrupt:
        sys.exit(130)

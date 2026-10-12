"""Serial client for the firmware test hooks (lvgl_ui/test_hooks.cpp).

The board must run the lvgl_ui_test build. Every reply line from the board starts
with "@@"; anything else (boot messages, calibration output) is logged and skipped.

A DeviceError means the test setup itself failed (no board, no reply, wrong
firmware). It is never sent to the AI as a UI bug.

A BoardCrash means the firmware crashed (ESP32 panic, e.g. a null pointer in the
generated UI). That IS a UI bug: the pipeline decodes the backtrace and sends it
back to the AI to fix.
"""

import json
import os
import re
import time

BAUD = 115200
# USB-serial chips found on ESP32 boards: CH9102/CH340 (1A86), CP210x (10C4), FTDI (0403).
KNOWN_VIDS = {0x1A86, 0x10C4, 0x0403}

# First line of an ESP32 crash report, and the line that ends it.
CRASH_START = re.compile(r"Guru Meditation Error|abort\(\) was called|Stack smashing protect|"
                         r"\*\*\*ERROR\*\*\* A stack overflow|assert failed:")
CRASH_END = re.compile(r"Backtrace:|Rebooting\.\.\.")


class DeviceError(Exception):
    pass


class BoardCrash(Exception):
    """The firmware crashed. report is the crash text from the serial port;
    addresses are the backtrace PCs, for decoding against firmware.elf."""

    def __init__(self, report: str):
        super().__init__(report.splitlines()[0] if report else "board crashed")
        self.report = report
        backtrace = next((l for l in report.splitlines() if "Backtrace:" in l), "")
        self.addresses = re.findall(r"(0x4[0-9a-fA-F]{7}):0x", backtrace)


def find_port() -> str:
    port = os.environ.get("UPLOAD_PORT")
    if port:
        return port
    from serial.tools import list_ports

    for p in list_ports.comports():
        if p.vid in KNOWN_VIDS:
            return p.device
    raise DeviceError("No ESP32 serial port found. Plug in the board or set UPLOAD_PORT in .env.")


class Device:
    def __init__(self, port: str = None, log=print, boot_timeout: float = 20.0):
        self.port = port or find_port()
        self.log = log
        self.boot_timeout = boot_timeout
        self.ser = None

    def __enter__(self):
        self.open()
        return self

    def __exit__(self, *exc):
        self.close()

    def open(self):
        import serial

        ser = serial.Serial()
        ser.port = self.port
        ser.baudrate = BAUD
        ser.timeout = 0.2
        # Keep DTR/RTS released so opening the port does not reset the ESP32 or
        # hold it in the bootloader.
        ser.dtr = False
        ser.rts = False
        try:
            ser.open()
        except serial.SerialException as e:
            raise DeviceError(f"Can't open {self.port}: {e}. Is another program (serial monitor) using it?") from None
        self.ser = ser

        # The board may still be booting after a flash: ping until it answers. A
        # UI that crashes at startup reboots in a loop, so its crash report shows
        # up here and raises BoardCrash.
        deadline = time.monotonic() + self.boot_timeout
        try:
            while time.monotonic() < deadline:
                try:
                    if self.command("PING", timeout=1.5, booting=True) == "OK":
                        return
                except DeviceError:
                    pass
        except BoardCrash:
            self.close()
            raise
        self.close()
        raise DeviceError(f"The board on {self.port} doesn't answer test commands. "
                          "Is the lvgl_ui_test firmware flashed?")

    def close(self):
        if self.ser:
            self.ser.close()
            self.ser = None

    def command(self, cmd: str, timeout: float = 5.0, booting: bool = False) -> str:
        """Sends one command; returns the reply without the "@@" prefix. Raises
        BoardCrash if a crash report arrives, or if the board restarts while
        running (booting=False)."""
        if not self.ser:
            raise DeviceError("serial port is not open")
        self.ser.reset_input_buffer()
        self.ser.write((cmd + "\n").encode("ascii"))
        crash = []  # lines of a crash report being received
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            raw = self.ser.readline()
            if not raw:
                continue
            line = raw.decode("utf-8", errors="replace").strip()
            if line.startswith("@@"):
                reply = line[2:]
                if reply == "READY":
                    if booting:
                        continue
                    raise BoardCrash("\n".join(crash) or f"The board restarted while running {cmd.split()[0]}")
                return reply
            if not line:
                continue
            self.log(f"[board] {line}")
            if crash or CRASH_START.search(line):
                crash.append(line)
                if CRASH_END.search(line) and len(crash) > 1:
                    raise BoardCrash("\n".join(crash))
        if crash:
            raise BoardCrash("\n".join(crash))
        raise DeviceError(f"No reply to {cmd.split()[0]} within {timeout:.0f}s")

    # ---- protocol ----

    def tree(self) -> dict:
        reply = self.command("TREE", timeout=10)
        if not reply.startswith("TREE "):
            raise DeviceError(f"unexpected TREE reply: {reply[:80]}")
        try:
            data = json.loads(reply[5:])
        except ValueError as e:
            raise DeviceError(f"TREE reply is not valid JSON ({e})") from None
        data["by_id"] = {w["id"]: w for w in data.get("widgets", [])}
        return data

    def tap(self, widget_id: str):
        """Returns None on success, or the board's reason for refusing."""
        reply = self.command(f"TAP_ID {widget_id}")
        return None if reply == "OK" else reply.removeprefix("ERR ").strip()

    def drag(self, widget_id: str, value: int):
        """Returns (value_reached, None) or (None, reason)."""
        reply = self.command(f"DRAG {widget_id} {int(value)}", timeout=10)
        if reply.startswith("OK value="):
            return int(reply.split("=", 1)[1]), None
        return None, reply.removeprefix("ERR ").strip()

    def wait_idle(self) -> bool:
        return self.command("WAIT_IDLE", timeout=6) == "IDLE"

    def reset_ui(self):
        if self.command("RESET_UI", timeout=10) != "OK":
            raise DeviceError("RESET_UI failed")

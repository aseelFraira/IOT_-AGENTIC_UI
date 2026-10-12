import unittest

import device_runner as d

CRASH = [
    "Guru Meditation Error: Core  1 panic'ed (StoreProhibited). Exception was unhandled.",
    "Core  1 register dump:",
    "EXCVADDR: 0x00000000  LBEG    : 0x400e2d69",
    "Backtrace: 0x400d205f:0x3ffb21e0 0x400d2202:0x3ffb2240 0x400d256d:0x3ffb2270",
    "Rebooting...",
]


class FakeSerial:
    def __init__(self, lines):
        self.lines = [l.encode() + b"\n" for l in lines]

    def reset_input_buffer(self):
        pass

    def write(self, data):
        pass

    def readline(self):
        return self.lines.pop(0) if self.lines else b""


def device(lines):
    dev = d.Device(port="COMX", log=lambda *_: None)
    dev.ser = FakeSerial(lines)
    return dev


class CommandTests(unittest.TestCase):
    def test_normal_reply_skips_other_output(self):
        self.assertEqual(device(["boot noise", "@@OK"]).command("PING"), "OK")

    def test_crash_report_raises_board_crash_with_addresses(self):
        with self.assertRaises(d.BoardCrash) as ctx:
            device(["@@READY"] + CRASH).command("PING", timeout=1, booting=True)
        self.assertIn("StoreProhibited", str(ctx.exception))
        self.assertEqual(ctx.exception.addresses, ["0x400d205f", "0x400d2202", "0x400d256d"])
        self.assertIn("EXCVADDR: 0x00000000", ctx.exception.report)

    def test_restart_mid_test_is_a_crash_but_not_while_booting(self):
        with self.assertRaises(d.BoardCrash):
            device(["@@READY", "@@OK"]).command("TAP_ID x")
        self.assertEqual(device(["@@READY", "@@OK"]).command("PING", booting=True), "OK")

    def test_silence_is_a_setup_error_not_a_crash(self):
        with self.assertRaises(d.DeviceError):
            device([]).command("PING", timeout=0.3)


if __name__ == "__main__":
    unittest.main()

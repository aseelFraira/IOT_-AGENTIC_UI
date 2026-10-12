import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pipeline
import spec
from test_spec import GOOD_SPEC
from test_spec_runner import FakeCamera, FakeDevice, judge_passing

CODE = """#include <lvgl.h>
#include "generated_ui.h"
#if UI_LIGHT_THEME
#define COL_BG lv_color_hex(0xF4F6F8)
#else
#define COL_BG lv_color_hex(0x101418)
#endif
static void cb(lv_event_t *) {}
void generated_ui_create(void) {
    lv_obj_t *b = lv_switch_create(lv_scr_act());
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, nullptr);
    ui_register(b, "sw_light");
}
void generated_ui_destroy(void) { lv_obj_clean(lv_scr_act()); }
"""


class ThemeFlowTests(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, tmp, ignore_errors=True)
        (tmp / "generated_ui.cpp").write_text("// previous UI\n", encoding="utf-8")
        (tmp / "ui_theme.h").write_text("#pragma once\n#define UI_LIGHT_THEME 0\n", encoding="utf-8")
        self.events = []  # ("compile"|"flash"|"test", theme value at that moment, camera used?)

        def theme():
            return "light" if "UI_LIGHT_THEME 1" in (tmp / "ui_theme.h").read_text() else "dark"

        def compile_ui(pio, env=None):
            self.events.append(("compile", theme()))
            return True, ""

        def flash_ui(pio, port, env=None, log=None):
            self.events.append(("flash", theme()))
            return True, ""

        self.light_device_ok = True

        def run_tests(the_spec, port, out_dir, log=print, judge=None, open_device=None,
                      open_camera=None, use_camera=True, must_see=None):
            self.events.append(("test", theme(), use_camera))
            broken = theme() == "light" and not self.light_device_ok
            import spec_runner
            return spec_runner.run_spec(the_spec, FakeDevice(broken_switch=broken),
                                        FakeCamera() if use_camera else None, out_dir,
                                        log=lambda *_: None, judge=judge_passing, must_see=must_see)

        for name, value in {"TARGET": tmp / "generated_ui.cpp", "THEME_FILE": tmp / "ui_theme.h",
                            "RUNS_DIR": tmp / "runs", "REJECTED_FILE": tmp / "rejected.cpp",
                            "compile_ui": compile_ui, "flash_ui": flash_ui, "run_tests": run_tests,
                            "find_pio": lambda: "pio"}.items():
            patcher = mock.patch.object(pipeline, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.tmp = tmp

    def run_with(self, the_spec):
        reply = spec.join_reply(CODE, json.dumps(the_spec))
        return pipeline.run("x", flash=True, test=True, log=lambda *_: None,
                            get_code=lambda s, m: reply)

    def test_light_is_switched_on_only_after_dark_checks_pass(self):
        r = self.run_with(dict(GOOD_SPEC, theme="light"))
        self.assertTrue(r.ok)
        self.assertEqual((r.theme, r.theme_wanted, r.tests), ("light", "light", "passed"))
        self.assertEqual(self.events, [
            ("compile", "dark"), ("flash", "dark"), ("test", "dark", True),   # camera sees dark
            ("compile", "light"), ("flash", "light"), ("test", "light", False),  # state checks only
        ])
        self.assertIn("UI_LIGHT_THEME 1", (self.tmp / "ui_theme.h").read_text())

    def test_failed_light_check_puts_dark_back(self):
        self.light_device_ok = False
        r = self.run_with(dict(GOOD_SPEC, theme="light"))
        self.assertTrue(r.ok)
        self.assertEqual((r.theme, r.theme_wanted), ("dark", "light"))
        self.assertEqual(self.events[-2:], [("compile", "dark"), ("flash", "dark")])
        self.assertIn("UI_LIGHT_THEME 0", (self.tmp / "ui_theme.h").read_text())

    def test_crash_goes_back_to_the_ai_and_is_not_accepted(self):
        import device_runner
        crash = device_runner.BoardCrash("Guru Meditation Error: Core 1 panic'ed (StoreProhibited)\n"
                                         "EXCVADDR: 0x00000000\nBacktrace: 0x400d205f:0x3ffb21e0")
        prompts = []
        reply = spec.join_reply(CODE, json.dumps(GOOD_SPEC))

        def get_code(system, messages):
            prompts.append(messages[-1][1])
            return reply

        with mock.patch.object(pipeline, "run_tests", side_effect=crash):
            r = pipeline.run("x", flash=True, test=True, log=lambda *_: None, get_code=get_code)
        self.assertFalse(r.ok)
        self.assertEqual(r.reply, "")  # the crashing UI is not kept
        self.assertEqual(len(prompts), pipeline.MAX_ATTEMPTS)
        self.assertIn("CRASHED", prompts[1])
        self.assertIn("null pointer", prompts[1])
        self.assertEqual((self.tmp / "generated_ui.cpp").read_text(), "// previous UI\n")

    def test_dark_ui_never_touches_light(self):
        r = self.run_with(GOOD_SPEC)
        self.assertEqual((r.theme, r.theme_wanted), ("dark", "dark"))
        self.assertEqual([e[1] for e in self.events], ["dark"] * 3)


if __name__ == "__main__":
    unittest.main()

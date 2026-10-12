import unittest
from pathlib import Path

import validator

HERE = Path(__file__).parent
DEFAULT_UI = HERE.parents[1] / "lvgl_ui" / "generated_ui.cpp"

USER_RULES = [
    "You generate only the LVGL user-interface implementation.",
    "Generate C++ code for generated_ui.cpp.",
    "Include lvgl.h and generated_ui.h.",
    "Define generated_ui_create() and generated_ui_destroy().",
    "Use only approved LVGL widgets.",
    "Do not use TFT_eSPI directly.",
    "Do not initialize the display, touch controller, LVGL or hardware pins.",
    "Do not define setup() or loop().",
    "Target the configured 240x320 display orientation.",
    "Keep every widget inside the visible screen.",
    "Avoid large animations and animated scrolling.",
]

GOOD = """
#include <lvgl.h>
#include "generated_ui.h"

static void cb(lv_event_t *) {}

void generated_ui_create(void) {
    lv_obj_t *b = lv_btn_create(lv_scr_act());
    lv_obj_set_size(b, 100, 40);
    lv_obj_set_pos(b, 20, 30);
    lv_obj_add_event_cb(b, cb, LV_EVENT_CLICKED, nullptr);
}

void generated_ui_destroy(void) { lv_obj_clean(lv_scr_act()); }
"""


def with_body(extra: str) -> str:
    return GOOD.replace("lv_obj_set_pos(b, 20, 30);", "lv_obj_set_pos(b, 20, 30);\n    " + extra)


class ValidatorTests(unittest.TestCase):
    def assertRejects(self, code, fragment):
        problems = validator.validate(code)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def test_good_code_passes(self):
        self.assertEqual(validator.validate(GOOD), [])

    def test_default_ui_passes(self):
        self.assertEqual(validator.validate(DEFAULT_UI.read_text(encoding="utf-8")), [])

    def test_prompt_contains_user_rules_verbatim(self):
        prompt = (HERE / "system_prompt.txt").read_text(encoding="utf-8")
        for rule in USER_RULES:
            self.assertIn(rule, prompt)

    def test_prompt_lists_every_approved_widget(self):
        prompt = (HERE / "system_prompt.txt").read_text(encoding="utf-8")
        for w in validator.APPROVED_WIDGETS:
            self.assertIn(f"lv_{w}_create", prompt)

    def test_missing_pieces(self):
        self.assertRejects("", "empty")
        self.assertRejects(GOOD.replace('#include "generated_ui.h"', ""), "generated_ui.h")
        self.assertRejects(GOOD.replace("#include <lvgl.h>", ""), "lvgl.h")
        self.assertRejects(GOOD.replace("generated_ui_destroy", "other"), "generated_ui_destroy")

    def test_forbidden_constructs(self):
        cases = {
            "tft.fillScreen(0);": "TFT_eSPI",
            "lv_init();": "initialises LVGL",
            "lv_disp_drv_init(nullptr);": "display or touch",
            "pinMode(2, OUTPUT);": "hardware pins",
            "Serial.println(1);": "hardware or network library",
            "delay(10);": "timing",
            "lv_anim_del(b, nullptr);": "animations",
            "lv_obj_scroll_to_y(b, 5, LV_ANIM_ON);": "animations",
            "lv_timer_create(nullptr, 1, nullptr);": "timers",
            "void *p = malloc(4);": "allocates memory",
            "lv_obj_set_style_text_font(b, &lv_font_montserrat_28, 0);": "font",
        }
        for stmt, fragment in cases.items():
            with self.subTest(stmt=stmt):
                self.assertRejects(with_body(stmt), fragment)

    def test_theme_switch_is_not_defined_by_the_ui(self):
        self.assertRejects("#define UI_LIGHT_THEME 1\n" + GOOD, "UI_LIGHT_THEME")
        self.assertEqual(validator.validate("#if UI_LIGHT_THEME\n#endif\n" + GOOD), [])

    def test_setup_and_loop_rejected(self):
        self.assertRejects(GOOD + "\nvoid setup() {}\n", "setup() or loop()")
        self.assertRejects(GOOD + "\nvoid loop() {}\n", "setup() or loop()")

    def test_unapproved_widget(self):
        self.assertRejects(with_body("lv_chart_create(lv_scr_act());"), "lv_chart_create")

    def test_approved_default_font_and_widgets_ok(self):
        code = with_body(
            "lv_obj_set_style_text_font(b, &lv_font_montserrat_14, 0);"
            "lv_slider_create(b); lv_switch_create(b); lv_led_create(b);"
        )
        self.assertEqual(validator.validate(code), [])

    def test_comments_and_strings_do_not_trigger(self):
        code = with_body('// uses tft and delay(5) here\n    lv_label_set_text(nullptr, "Serial delay(1) tft.x");')
        self.assertEqual(validator.validate(code), [])

    def test_comment_markers_inside_strings_and_chars_do_not_hide_code(self):
        # A "/*" inside a string used to start a fake block comment that swallowed
        # generated_ui_destroy(); calculators hit this with their operator list.
        tricky = GOOD.replace(
            "void generated_ui_create(void) {",
            'static const char *OPS = "/*-+";\n'
            "static const char QUOTE = '\"';\n"
            'static const char *URL = "http://x";\n'
            "void generated_ui_create(void) {",
        )
        tricky = tricky.replace(
            "void generated_ui_destroy(void)",
            "/* real comment */ void generated_ui_destroy(void)",
        )
        self.assertEqual(validator.validate(tricky), [])

    def test_forbidden_code_after_tricky_literals_is_still_caught(self):
        tricky = with_body('const char *o = "/*-+"; delay(5);')
        self.assertRejects(tricky, "timing")

    def test_bounds(self):
        self.assertRejects(with_body("lv_obj_set_size(b, 300, 40);"), "wider")
        self.assertRejects(with_body("lv_obj_set_size(b, 100, 400);"), "taller")
        self.assertRejects(with_body("lv_obj_set_pos(b, 250, 10);"), "x=250")
        self.assertRejects(with_body("lv_obj_set_pos(b, 10, 320);"), "y=320")
        self.assertRejects(with_body("lv_obj_set_pos(b, -5, 10);"), "x=-5")
        self.assertEqual(validator.validate(with_body("lv_obj_set_pos(b, 239, 319);")), [])

    def test_strip_fences(self):
        fenced = "```cpp\n" + GOOD.strip() + "\n```"
        self.assertEqual(validator.validate(validator.strip_fences(fenced)), [])
        self.assertRejects("```cpp\n" + GOOD + "\nextra ```", "fence")


class DotenvTests(unittest.TestCase):
    def test_loads_values_but_real_environment_wins(self):
        import os
        import tempfile
        import gemini_client

        with tempfile.TemporaryDirectory() as d:
            env = Path(d) / ".env"
            env.write_text(
                '# comment\nT_KEY_A = "quoted value"\nT_KEY_B=plain\nT_KEY_C=\nnot a pair\nT_KEY_D=fromfile\n',
                encoding="utf-8",
            )
            os.environ["T_KEY_D"] = "fromshell"
            try:
                gemini_client.load_dotenv(env)
                self.assertEqual(os.environ["T_KEY_A"], "quoted value")
                self.assertEqual(os.environ["T_KEY_B"], "plain")
                self.assertNotIn("T_KEY_C", os.environ)
                self.assertEqual(os.environ["T_KEY_D"], "fromshell")
            finally:
                for k in ("T_KEY_A", "T_KEY_B", "T_KEY_C", "T_KEY_D"):
                    os.environ.pop(k, None)

    def test_missing_file_is_fine(self):
        import gemini_client
        gemini_client.load_dotenv(Path("does-not-exist.env"))


if __name__ == "__main__":
    unittest.main()

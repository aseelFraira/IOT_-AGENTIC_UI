import copy
import json
import unittest

import spec

GOOD_SPEC = {
    "intent": "a light switch",
    "visual_rubric": ["A switch is visible"],
    "tests": [
        {"name": "toggle on", "steps": [
            {"capture": {"label": "before", "expect_visual": ["The switch is off"]}},
            {"tap": "sw_light"},
            {"expect": {"sw_light": {"checked": True}, "lbl_state": {"text": "On"}}},
            {"capture": {"label": "after", "expect_visual": ["The switch is on"]}},
        ]},
        {"name": "dim", "steps": [
            {"drag": ["sl_bright", 30]},
            {"wait_ms": 100},
            {"expect": {"sl_bright": {"value": 30}, "lbl_value": {"text_contains": "30"}}},
        ]},
    ],
}
CODE = '#include "generated_ui.h"\nvoid f(){ ui_register(o, "sw_light"); }\n'


class SplitTests(unittest.TestCase):
    def test_round_trip(self):
        reply = spec.join_reply(CODE, json.dumps(GOOD_SPEC))
        code, spec_text, problems = spec.split_reply(reply)
        self.assertEqual(problems, [])
        self.assertEqual(code.strip(), CODE.strip())
        self.assertEqual(json.loads(spec_text), GOOD_SPEC)

    def test_fenced_sections_and_crlf(self):
        reply = ("===generated_ui.cpp===\r\n```cpp\r\n" + CODE + "```\r\n"
                 "===ui_spec.json===\r\n```json\r\n" + json.dumps(GOOD_SPEC) + "\r\n```\r\n")
        code, spec_text, problems = spec.split_reply(reply)
        self.assertEqual(problems, [])
        self.assertNotIn("```", code)
        self.assertEqual(json.loads(spec_text), GOOD_SPEC)

    def test_missing_spec_section(self):
        _, _, problems = spec.split_reply(CODE)
        self.assertTrue(any("ui_spec.json" in p for p in problems))


class ValidateTests(unittest.TestCase):
    def problems_after(self, change):
        s = copy.deepcopy(GOOD_SPEC)
        change(s)
        return spec.validate(s)

    def assertProblem(self, change, fragment):
        problems = self.problems_after(change)
        self.assertTrue(any(fragment in p for p in problems), f"{fragment!r} not in {problems}")

    def test_good_spec(self):
        self.assertEqual(spec.validate(GOOD_SPEC), [])
        self.assertEqual(spec.parse(json.dumps(GOOD_SPEC)), (GOOD_SPEC, []))

    def test_bad_json(self):
        s, problems = spec.parse("{not json")
        self.assertIsNone(s)
        self.assertIn("not valid JSON", problems[0])

    def test_shape_problems(self):
        self.assertProblem(lambda s: s.pop("intent"), "intent")
        self.assertProblem(lambda s: s.update(tests=[]), "at least one test")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append({"click": "x"}), "a step must be one of")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append({"tap": "Bad-Id"}), "tap needs a widget id")
        self.assertProblem(lambda s: s["tests"][1]["steps"].__setitem__(0, {"drag": ["sl_bright", "30"]}), "drag must be")
        self.assertProblem(lambda s: s["tests"][1]["steps"].__setitem__(1, {"wait_ms": 99999}), "wait_ms")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append({"expect": {"sw_light": {"colour": "red"}}}),
                           "unknown property")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append({"expect": {"sw_light": {"checked": "yes"}}}),
                           "checked must be bool")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append({"capture": {"label": "x", "expect_visual": []}}),
                           "expect_visual")
        self.assertProblem(lambda s: s["tests"][1].update(name="toggle on"), "duplicate test name")
        self.assertProblem(lambda s: s["tests"][1].update(steps=[{"tap": "sw_light"}]), "checks nothing")
        self.assertProblem(lambda s: s["tests"][0]["steps"].append(
            {"capture": {"label": "before", "expect_visual": ["x"]}}), "labels must be unique")

    def test_budget(self):
        many = [{"capture": {"label": f"c{i}", "expect_visual": ["x"]}} for i in range(spec.MAX_CAPTURES_PER_TEST + 1)]
        self.assertProblem(lambda s: s["tests"][0].update(steps=many), "too many captures")
        self.assertProblem(lambda s: s.update(tests=[{"name": f"t{i}", "steps": [{"expect": {"a": {"visible": True}}}]}
                                                     for i in range(spec.MAX_TESTS + 1)]), "too many tests")

    def test_bool_is_not_int(self):
        self.assertProblem(lambda s: s["tests"][1]["steps"].__setitem__(0, {"drag": ["sl_bright", True]}), "drag must be")

    def test_theme(self):
        self.assertProblem(lambda s: s.update(theme="blue"), "theme")
        light = dict(GOOD_SPEC, theme="light")
        self.assertEqual(spec.validate(light), [])
        self.assertTrue(any("UI_LIGHT_THEME" in p for p in spec.check_against_code(light, CODE)))
        both = CODE + "#if UI_LIGHT_THEME\n#define BG 1\n#else\n#define BG 0\n#endif\n"
        self.assertEqual(spec.check_against_code(light, both), [])
        self.assertEqual(spec.check_against_code(GOOD_SPEC, CODE), [])  # dark needs no palettes

    def test_ids_and_code_cross_check(self):
        self.assertEqual(spec.ids_used(GOOD_SPEC), {"sw_light", "lbl_state", "sl_bright", "lbl_value"})
        self.assertEqual(spec.check_against_code(GOOD_SPEC, CODE), [])
        self.assertTrue(spec.check_against_code(GOOD_SPEC, "void f(){}"))


if __name__ == "__main__":
    unittest.main()

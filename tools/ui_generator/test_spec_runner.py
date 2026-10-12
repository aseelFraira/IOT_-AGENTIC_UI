import json
import shutil
import tempfile
import unittest
from pathlib import Path

import judge
import spec_runner
from camera import CameraError
from test_spec import GOOD_SPEC


class FakeDevice:
    """Simulates the switch/slider UI from GOOD_SPEC over the test protocol."""

    def __init__(self, register=("sw_light", "lbl_state", "sl_bright", "lbl_value"), broken_switch=False):
        self.register = register
        self.broken_switch = broken_switch
        self.log = []
        self.reset_ui()

    def reset_ui(self):
        self.log.append("RESET_UI")
        self.state = {"checked": False, "value": 50}

    def wait_idle(self):
        return True

    def tree(self):
        widgets = {
            "sw_light": {"type": "switch", "x": 10, "y": 10, "w": 50, "h": 30, "visible": True,
                         "checked": self.state["checked"], "disabled": False},
            "lbl_state": {"type": "label", "x": 70, "y": 10, "w": 40, "h": 20, "visible": True,
                          "checked": False, "disabled": False, "text": "On" if self.state["checked"] else "Off"},
            "sl_bright": {"type": "slider", "x": 10, "y": 60, "w": 200, "h": 20, "visible": True,
                          "checked": False, "disabled": False, "value": self.state["value"]},
            "lbl_value": {"type": "label", "x": 10, "y": 90, "w": 60, "h": 20, "visible": True,
                          "checked": False, "disabled": False, "text": f"{self.state['value']}%"},
        }
        listed = [dict(id=i, **widgets[i]) for i in self.register]
        return {"screen": [240, 320], "widgets": listed, "overflow": False, "by_id": {w["id"]: w for w in listed}}

    def tap(self, wid):
        self.log.append(f"TAP {wid}")
        if wid == "sw_light" and not self.broken_switch:
            self.state["checked"] = not self.state["checked"]
        return None

    def drag(self, wid, value):
        self.log.append(f"DRAG {wid} {value}")
        self.state["value"] = value
        return value, None


class FakeCamera:
    def __init__(self):
        self.shots = []

    def capture(self, path):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\xff\xd8 fake jpeg")
        self.shots.append(path)
        return path


def judge_passing(name, intent, captures, **_):
    verdict = {"test": name, "captures": [{"label": c["label"], "criteria": []} for c in captures]}
    return [], verdict


class RunSpecTests(unittest.TestCase):
    def setUp(self):
        self.out = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.out, ignore_errors=True)

    def test_everything_passes_with_photos(self):
        cam = FakeCamera()
        calls = []

        def judge_fn(name, intent, captures, **_):
            calls.append((name, [c["label"] for c in captures], [c["after"] for c in captures]))
            return judge_passing(name, intent, captures)

        r = spec_runner.run_spec(GOOD_SPEC, FakeDevice(), cam, self.out, log=lambda *_: None, judge=judge_fn)
        self.assertTrue(r["passed"], r["failures"])
        self.assertEqual(r["photos"], 3)  # initial + before + after
        self.assertEqual(calls[0][0], "initial render")
        self.assertEqual(calls[1], ("toggle on", ["before", "after"], ["the UI was started", "tapping sw_light"]))
        self.assertEqual(len(calls), 2)  # the "dim" test has no photos, so no judge call
        self.assertTrue((self.out / "toggle_on" / "04_after" / "photo.jpg").exists())
        self.assertTrue((self.out / "toggle_on" / "04_after" / "tree.json").exists())
        self.assertTrue(json.loads((self.out / "result.json").read_text())["passed"])

    def test_each_test_starts_from_a_fresh_ui(self):
        dev = FakeDevice()
        spec_runner.run_spec(GOOD_SPEC, dev, None, self.out, log=lambda *_: None, judge=judge_passing)
        self.assertEqual(dev.log.count("RESET_UI"), 1 + 1 + len(GOOD_SPEC["tests"]))

    def test_state_failure_stops_the_test_and_skips_its_photos(self):
        cam = FakeCamera()
        judged = []
        r = spec_runner.run_spec(GOOD_SPEC, FakeDevice(broken_switch=True), cam, self.out, log=lambda *_: None,
                                 judge=lambda n, i, c, **_: (judged.append(n), judge_passing(n, i, c))[1])
        self.assertFalse(r["passed"])
        f = r["failures"][0]
        self.assertEqual((f["kind"], f["test"], f["step"], f["id"]), ("state", "toggle on", 3, "sw_light"))
        self.assertNotIn("toggle on", judged)
        self.assertEqual(r["tests"][0]["passed"], False)
        self.assertEqual(r["tests"][1]["passed"], True)
        self.assertIn("expected checked=True", spec_runner.describe_failures(r["failures"]))

    def test_unregistered_ids_fail_before_running(self):
        dev = FakeDevice(register=("sw_light",))
        r = spec_runner.run_spec(GOOD_SPEC, dev, FakeCamera(), self.out, log=lambda *_: None, judge=judge_passing)
        self.assertFalse(r["passed"])
        self.assertEqual({f["id"] for f in r["failures"]}, {"lbl_state", "sl_bright", "lbl_value"})
        self.assertNotIn("TAP sw_light", dev.log)

    def test_visual_failures_are_reported(self):
        def judge_fn(name, intent, captures, **_):
            if name != "toggle on":
                return judge_passing(name, intent, captures)
            return [{"kind": "visual", "test": name, "label": "after", "expected": "The switch is on",
                     "actual": "switch looks grey", "photo": "p.jpg"}], {}

        r = spec_runner.run_spec(GOOD_SPEC, FakeDevice(), FakeCamera(), self.out, log=lambda *_: None, judge=judge_fn)
        self.assertFalse(r["passed"])
        text = spec_runner.describe_failures(r["failures"])
        self.assertIn("photo 'after'", text)
        self.assertIn("switch looks grey", text)

    def test_layout_check(self):
        dev = FakeDevice()
        orig = dev.tree

        def tree():
            t = orig()
            t["by_id"]["sl_bright"]["w"] = 400
            return t

        dev.tree = tree
        r = spec_runner.run_spec(GOOD_SPEC, dev, None, self.out, log=lambda *_: None, judge=judge_passing)
        self.assertTrue(any(f["kind"] == "layout" and f["id"] == "sl_bright" for f in r["failures"]))


class JudgeTests(unittest.TestCase):
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.dir, ignore_errors=True)
        (self.dir / "a.jpg").write_bytes(b"\xff\xd8 a")
        (self.dir / "b.jpg").write_bytes(b"\xff\xd8 b")
        self.caps = [
            {"label": "before", "photo": self.dir / "a.jpg", "after": "start", "expect_visual": ["off", "title"]},
            {"label": "after", "photo": self.dir / "b.jpg", "after": "tapping sw", "expect_visual": ["on"]},
        ]

    def run_judge(self, replies):
        sent = []

        def generate(system_prompt, messages, **kw):
            sent.append((messages, kw))
            return replies.pop(0)

        return judge.judge_test("toggle", "a switch", self.caps, generate=generate), sent

    def test_parses_verdict_and_sends_images_in_order(self):
        reply = json.dumps({"captures": [
            {"label": "before", "screen_visible": True, "criteria": [
                {"criterion": "off", "pass": True, "reason": "grey"},
                {"criterion": "title", "pass": False, "reason": "no title visible"}]},
            {"label": "after", "screen_visible": True, "criteria": [
                {"criterion": "on", "pass": True, "reason": "blue"}]}]})
        (failures, verdict), sent = self.run_judge([reply])
        self.assertEqual([(f["label"], f["expected"], f["actual"]) for f in failures],
                         [("before", "title", "no title visible")])
        parts = sent[0][0][0][1]
        images = [p for p in parts if "inline_data" in p]
        self.assertEqual(len(images), 2)
        self.assertTrue(sent[0][1]["json_output"])
        self.assertEqual(sent[0][1]["temperature"], 0.0)

    def test_missing_answers_count_as_failures(self):
        reply = json.dumps({"captures": [{"label": "before", "screen_visible": True, "criteria": []}]})
        (failures, _), _ = self.run_judge([reply])
        self.assertEqual(len(failures), 3)

    def test_retries_once_on_bad_json(self):
        good = json.dumps({"captures": [{"label": "before", "criteria": [{"pass": True}, {"pass": True}]},
                                        {"label": "after", "criteria": [{"pass": True}]}]})
        (failures, _), sent = self.run_judge(["not json", good])
        self.assertEqual(failures, [])
        self.assertEqual(len(sent), 2)

    def test_bad_framing_is_a_setup_error_only_when_checked(self):
        for field in ("fully_in_frame", "upright"):
            reply = json.dumps({"captures": [
                {"label": "before", field: False, "criteria": [{"pass": True}, {"pass": True}]},
                {"label": "after", "criteria": [{"pass": True}]}]})
            (failures, _), _ = self.run_judge([reply])  # later photos: no framing check
            self.assertEqual(failures, [])
            with self.assertRaises(CameraError):
                judge.judge_test("initial render", "x", self.caps, check_framing=True,
                                 generate=lambda *a, **k: reply)

    def test_camera_not_seeing_the_screen_is_a_setup_error(self):
        reply = json.dumps({"captures": [{"label": "before", "screen_visible": False, "criteria": []}]})
        with self.assertRaises(CameraError):
            self.run_judge([reply])


if __name__ == "__main__":
    unittest.main()

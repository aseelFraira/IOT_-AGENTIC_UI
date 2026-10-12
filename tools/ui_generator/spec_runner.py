"""Runs a ui_spec.json on the real board and collects the evidence.

Order of checks, cheapest and most exact first:
1. Every widget id the tests use is registered on the board (TREE).
2. Every visible registered widget lies fully inside the screen.
3. Per test: RESET_UI, then the steps. "expect" steps compare the board's widget
   state exactly; the first failing step ends that test. "capture" steps wait for
   the screen to settle and take a photo.
4. Per test with photos: one vision-judge call with all of that test's photos.
Plus an implicit "initial render" photo, judged against the intent and rubric.

Results are written under out_dir. DeviceError / CameraError propagate: they mean
the setup is broken, not the UI.
"""

import json
import re
import time
from pathlib import Path

import judge as judge_module
import spec as spec_module


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_-]+", "_", text).strip("_") or "x"


def _save_json(path: Path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {k: v for k, v in data.items() if k != "by_id"} if isinstance(data, dict) else data
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")


def check_expect(expect: dict, tree: dict, test: str, step: int):
    """Returns (failures, number_of_checks)."""
    failures, checks = [], 0
    for wid, props in expect.items():
        widget = tree["by_id"].get(wid)
        for prop, wanted in props.items():
            checks += 1
            if widget is None:
                actual, ok = "widget not found", False
            elif prop == "text_contains":
                actual = widget.get("text")
                ok = isinstance(actual, str) and wanted in actual
            else:
                actual = widget.get(prop, f"{widget['type']} has no {prop}")
                ok = actual == wanted
            if not ok:
                failures.append({"kind": "state", "test": test, "step": step, "id": wid,
                                 "expected": f"{prop}={wanted!r}", "actual": repr(actual)})
    return failures, checks


def check_layout(tree: dict):
    """Returns (failures, number_of_checks) for widgets sticking out of the screen."""
    sw, sh = tree.get("screen", [240, 320])
    failures, checks = [], 0
    for w in tree.get("widgets", []):
        if not w.get("visible"):
            continue
        checks += 1
        if w["x"] < 0 or w["y"] < 0 or w["x"] + w["w"] > sw or w["y"] + w["h"] > sh:
            failures.append({"kind": "layout", "test": "layout", "id": w["id"],
                             "expected": f"inside the {sw}x{sh} screen",
                             "actual": f"x={w['x']} y={w['y']} w={w['w']} h={w['h']}"})
    return failures, checks


class Report:
    def __init__(self):
        self.failures, self.tests = [], []
        self.checks_total = self.checks_passed = 0
        self.photos = 0

    def add(self, failures, checks):
        self.failures += failures
        self.checks_total += checks
        self.checks_passed += checks - len(failures)

    def as_dict(self, visual: bool):
        return {"passed": not self.failures, "checks_total": self.checks_total,
                "checks_passed": self.checks_passed, "visual": visual, "photos": self.photos,
                "failures": self.failures, "tests": self.tests}


def run_spec(spec: dict, device, camera, out_dir: Path, log=print, judge=None, must_see=None) -> dict:
    """camera may be None: then capture steps are skipped and only exact checks run.
    must_see: independent requirements (requirements.py) the first photo must meet,
    on top of the builder's own description."""
    judge = judge or judge_module.judge_test
    intent = spec["intent"]
    report = Report()

    device.reset_ui()
    device.wait_idle()
    tree = device.tree()
    _save_json(out_dir / "initial" / "tree.json", tree)

    if tree.get("overflow"):
        report.add([{"kind": "spec", "test": "setup", "expected": "at most 128 registered widgets",
                     "actual": "ui_register was called for too many widgets"}], 1)
    missing = sorted(spec_module.ids_used(spec) - set(tree["by_id"]))
    report.add([{"kind": "spec", "test": "setup", "id": wid,
                 "expected": f"a widget registered with ui_register(obj, \"{wid}\")",
                 "actual": "no widget has this id"} for wid in missing], len(spec_module.ids_used(spec)))
    if missing:
        log(f"Tests use unregistered widget ids: {', '.join(missing)}; not running the tests.")
        result = report.as_dict(camera is not None)
        _save_json(out_dir / "result.json", result)
        return result
    report.add(*check_layout(tree))

    if camera is not None:
        photo = camera.capture(out_dir / "initial" / "photo.jpg")
        report.photos += 1
        shows = f"The display shows: {intent}"
        if spec.get("theme") == "light":
            # The photos are of the dark test version; the light colours come later.
            shows += " (this photo is its dark-colour test version, so ignore colour words such as white or light)"
        criteria = ([shows] + list(must_see or []) + list(spec.get("visual_rubric", []))
                    + judge_module.DEFAULT_RUBRIC)
        caps = [{"label": "initial", "photo": photo, "after": "the UI was started", "expect_visual": criteria}]
        # The first photo also checks the camera framing: a cut-off or rotated view
        # raises CameraError (setup problem) instead of failing the UI.
        failures, verdict = judge("initial render", intent, caps, check_framing=True)
        _save_json(out_dir / "initial" / "verdict.json", verdict)
        report.add(failures, len(criteria))
        log(f"Initial render: {len(criteria) - len(failures)}/{len(criteria)} visual checks passed.")

    for test in spec["tests"]:
        name = test["name"]
        test_dir = out_dir / _slug(name)
        before = len(report.failures)
        device.reset_ui()
        device.wait_idle()
        after = "the UI was started"
        captures = []
        stopped = False

        for n, step in enumerate(test["steps"], 1):
            kind, arg = next(iter(step.items()))
            if kind == "tap":
                reason = device.tap(arg)
                if reason:
                    report.add([{"kind": "state", "test": name, "step": n, "id": arg,
                                 "expected": f"{arg} can be tapped", "actual": reason}], 1)
                    stopped = True
                after = f"tapping {arg}"
            elif kind == "drag":
                wid, value = arg
                reached, reason = device.drag(wid, value)
                if reason:
                    report.add([{"kind": "state", "test": name, "step": n, "id": wid,
                                 "expected": f"{wid} can be dragged to {value}", "actual": reason}], 1)
                    stopped = True
                after = f"dragging {wid} to {value}"
            elif kind == "wait_ms":
                time.sleep(arg / 1000)
            elif kind == "expect":
                device.wait_idle()
                failures, checks = check_expect(arg, device.tree(), name, n)
                report.add(failures, checks)
                stopped = bool(failures)
            elif kind == "capture" and camera is not None:
                if not device.wait_idle():
                    log(f"  {name}: screen still changing before photo {arg['label']!r}")
                cap_dir = test_dir / f"{n:02d}_{_slug(arg['label'])}"
                _save_json(cap_dir / "tree.json", device.tree())
                photo = camera.capture(cap_dir / "photo.jpg")
                report.photos += 1
                captures.append({"label": arg["label"], "photo": photo, "after": after,
                                 "expect_visual": arg["expect_visual"]})
            if stopped:
                log(f"  {name}: stopped at step {n} ({kind})")
                break

        if captures and not stopped:
            failures, verdict = judge(name, intent, captures)
            _save_json(test_dir / "verdict.json", verdict)
            report.add(failures, sum(len(c["expect_visual"]) for c in captures))

        passed = len(report.failures) == before
        report.tests.append({"name": name, "passed": passed, "photos": len(captures)})
        log(f"Test {name!r}: {'passed' if passed else 'FAILED'}"
            + (f" ({len(captures)} photos)" if captures else ""))

    result = report.as_dict(camera is not None)
    _save_json(out_dir / "result.json", result)
    return result


def describe_failures(failures, limit: int = 12) -> str:
    """Plain text for Gemini's retry prompt."""
    lines = []
    for f in failures[:limit]:
        where = f"test {f['test']!r}"
        if "step" in f:
            where += f", step {f['step']}"
        if "label" in f:
            where += f", photo {f['label']!r}"
        subject = f" widget {f['id']}:" if f.get("id") else ""
        if f["kind"] == "visual":
            lines.append(f"- [{where}] camera check failed: expected \"{f['expected']}\"; the judge saw: {f['actual']}")
        else:
            lines.append(f"- [{where}]{subject} expected {f['expected']}, got {f['actual']}")
    if len(failures) > limit:
        lines.append(f"- ... and {len(failures) - limit} more")
    return "\n".join(lines)

"""ui_spec.json: the tests Gemini writes for every UI it generates.

Gemini returns two files in one reply, each after a marker line:

    ===generated_ui.cpp===
    <C++>
    ===ui_spec.json===
    <JSON>

The spec is data, not code: spec_runner.py carries out its steps on the board.
This module only splits the reply and checks the spec's shape and budget.
"""

import json
import re

import validator

CODE_MARKER = "===generated_ui.cpp==="
SPEC_MARKER = "===ui_spec.json==="

MAX_TESTS = 10
MAX_STEPS_PER_TEST = 20
MAX_CAPTURES_PER_TEST = 6
MAX_CAPTURES_TOTAL = 30
MAX_WAIT_MS = 3000

ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,22}$")  # fits the firmware's 24-byte id buffer
NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9 _\-]{0,47}$")

# Widget properties an "expect" step may check, with their JSON type.
EXPECT_PROPS = {
    "checked": bool,
    "disabled": bool,
    "visible": bool,
    "value": int,
    "text": str,
    "text_contains": str,
}

STEP_KINDS = ("tap", "drag", "wait_ms", "expect", "capture")


def split_reply(reply: str):
    """Returns (code, spec_text, problems)."""
    text = reply.replace("\r\n", "\n")
    if SPEC_MARKER not in text:
        return validator.strip_fences(text), "", [f"missing the {SPEC_MARKER} section"]
    code_part, spec_part = text.split(SPEC_MARKER, 1)
    if CODE_MARKER in code_part:
        code_part = code_part.split(CODE_MARKER, 1)[1]
    return validator.strip_fences(code_part), validator.strip_fences(spec_part).strip(), []


def join_reply(code: str, spec_text: str) -> str:
    """The inverse of split_reply (used for history and for --code-file)."""
    return f"{CODE_MARKER}\n{code.strip()}\n{SPEC_MARKER}\n{spec_text.strip()}\n"


def parse(spec_text: str):
    """Returns (spec, problems). spec is None when the JSON can't be read."""
    try:
        spec = json.loads(spec_text)
    except ValueError as e:
        return None, [f"ui_spec.json is not valid JSON: {e}"]
    return spec, validate(spec)


def captures(test: dict):
    return [s["capture"] for s in test.get("steps", []) if isinstance(s, dict) and "capture" in s]


def _check_step(step, where, problems):
    if not isinstance(step, dict) or len(step) != 1 or next(iter(step)) not in STEP_KINDS:
        problems.append(f"{where}: a step must be one of {', '.join(STEP_KINDS)}")
        return
    kind, arg = next(iter(step.items()))
    if kind == "tap":
        if not isinstance(arg, str) or not ID_RE.match(arg):
            problems.append(f"{where}: tap needs a widget id")
    elif kind == "drag":
        if (not isinstance(arg, list) or len(arg) != 2 or not isinstance(arg[0], str)
                or not ID_RE.match(arg[0]) or type(arg[1]) is not int):
            problems.append(f'{where}: drag must be ["widget_id", integer_value]')
    elif kind == "wait_ms":
        if type(arg) is not int or not 0 < arg <= MAX_WAIT_MS:
            problems.append(f"{where}: wait_ms must be 1..{MAX_WAIT_MS}")
    elif kind == "expect":
        if not isinstance(arg, dict) or not arg:
            problems.append(f"{where}: expect must map widget ids to properties")
            return
        for wid, props in arg.items():
            if not ID_RE.match(wid):
                problems.append(f"{where}: bad widget id {wid!r}")
            if not isinstance(props, dict) or not props:
                problems.append(f"{where}: expect.{wid} must be an object of properties")
                continue
            for prop, value in props.items():
                if prop not in EXPECT_PROPS:
                    problems.append(f"{where}: unknown property {prop!r} (allowed: {', '.join(EXPECT_PROPS)})")
                elif type(value) is not EXPECT_PROPS[prop]:
                    problems.append(f"{where}: {prop} must be {EXPECT_PROPS[prop].__name__}")
    elif kind == "capture":
        if not isinstance(arg, dict) or not isinstance(arg.get("label"), str) or not NAME_RE.match(arg["label"]):
            problems.append(f"{where}: capture needs a short label (letters, digits, space, _ or -)")
        visual = arg.get("expect_visual") if isinstance(arg, dict) else None
        if not isinstance(visual, list) or not visual or not all(isinstance(v, str) and v.strip() for v in visual):
            problems.append(f"{where}: capture.expect_visual must be a non-empty list of sentences")


def validate(spec):
    """Returns a list of problems. Empty means the spec is usable."""
    if not isinstance(spec, dict):
        return ["ui_spec.json must be a JSON object"]
    problems = []
    if not isinstance(spec.get("intent"), str) or not spec["intent"].strip():
        problems.append('ui_spec.json needs "intent": one sentence describing the screen')
    if spec.get("theme", "dark") not in ("dark", "light"):
        problems.append('"theme" must be "dark" or "light" (the colours the user wants)')
    rubric = spec.get("visual_rubric", [])
    if not isinstance(rubric, list) or not all(isinstance(r, str) for r in rubric):
        problems.append("visual_rubric must be a list of sentences")

    tests = spec.get("tests")
    if not isinstance(tests, list):
        return problems + ['ui_spec.json needs a "tests" list']
    if not tests:
        problems.append("tests is empty: write at least one test")
    if len(tests) > MAX_TESTS:
        problems.append(f"too many tests ({len(tests)}); at most {MAX_TESTS}")

    names = set()
    total_captures = 0
    for i, test in enumerate(tests, 1):
        where = f"test {i}"
        if not isinstance(test, dict):
            problems.append(f"{where} must be an object")
            continue
        name = test.get("name")
        if not isinstance(name, str) or not NAME_RE.match(name):
            problems.append(f"{where}: needs a short name (letters, digits, space, _ or -)")
        elif name in names:
            problems.append(f"{where}: duplicate test name {name!r}")
        else:
            names.add(name)
            where = f"test {name!r}"
        steps = test.get("steps")
        if not isinstance(steps, list) or not steps:
            problems.append(f"{where}: needs a non-empty steps list")
            continue
        if len(steps) > MAX_STEPS_PER_TEST:
            problems.append(f"{where}: too many steps ({len(steps)}); at most {MAX_STEPS_PER_TEST}")
        for j, step in enumerate(steps, 1):
            _check_step(step, f"{where} step {j}", problems)

        kinds = {next(iter(s)) for s in steps if isinstance(s, dict) and len(s) == 1}
        if not kinds & {"expect", "capture"}:
            problems.append(f"{where}: checks nothing; add an expect or capture step")
        caps = captures(test)
        labels = [c.get("label") for c in caps if isinstance(c, dict)]
        if len(labels) != len(set(labels)):
            problems.append(f"{where}: capture labels must be unique within a test")
        if len(caps) > MAX_CAPTURES_PER_TEST:
            problems.append(f"{where}: too many captures ({len(caps)}); at most {MAX_CAPTURES_PER_TEST}")
        total_captures += len(caps)

    if total_captures > MAX_CAPTURES_TOTAL:
        problems.append(f"too many captures in total ({total_captures}); at most {MAX_CAPTURES_TOTAL}")
    return problems


def ids_used(spec: dict):
    """Every widget id the tests refer to."""
    ids = set()
    for test in spec.get("tests", []):
        for step in test.get("steps", []):
            if "tap" in step:
                ids.add(step["tap"])
            elif "drag" in step:
                ids.add(step["drag"][0])
            elif "expect" in step:
                ids.update(step["expect"])
    return ids


def check_against_code(spec: dict, code: str):
    """Cheap static cross-check. Ids built at runtime (snprintf in a loop) can't
    be seen here; the device's TREE is the real check."""
    problems = []
    if ids_used(spec) and "ui_register" not in code:
        problems.append("the tests use widget ids but generated_ui.cpp never calls ui_register()")
    if spec.get("theme") == "light" and not re.search(r"#\s*if\s+UI_LIGHT_THEME\b|\bUI_LIGHT_THEME\s*\?", code):
        problems.append('"theme" is "light" but generated_ui.cpp never chooses its colours with '
                        "UI_LIGHT_THEME (#if UI_LIGHT_THEME ... #else ... #endif)")
    return problems

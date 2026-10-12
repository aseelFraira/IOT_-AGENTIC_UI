"""Request -> Gemini -> validate -> compile -> flash -> test on the board -> camera judge.

Gemini returns generated_ui.cpp plus ui_spec.json (the tests for that UI). With
test=True the board gets the lvgl_ui_test build, spec_runner.py carries out the
spec over serial, the camera photographs each capture step and the vision judge
checks the photos. Any failed check goes back to Gemini, up to MAX_ATTEMPTS.

generated_ui.cpp is only kept when it passed everything that ran. If every
attempt fails, the previous generated_ui.cpp is restored, and put back on the
board if a failed attempt had been flashed.

A broken test setup (no board reply, no camera picture) is not the UI's fault:
the UI is kept and the result says the tests did not run.

Every run is recorded under tools/ui_generator/runs/<timestamp>/.
"""

import contextlib
import json
import os
import re
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import camera
import device_runner
import gemini_client
import openai_client
import requirements
import spec as spec_module
import spec_runner
import validator

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[1]
TARGET = ROOT / "lvgl_ui" / "generated_ui.cpp"
THEME_FILE = ROOT / "lvgl_ui" / "ui_theme.h"
PROMPT_FILE = HERE / "system_prompt.txt"
REJECTED_FILE = HERE / "last_rejected.cpp"  # latest reply that broke a rule
RUNS_DIR = HERE / "runs"
PIO_ENV = "lvgl_ui"
TEST_ENV = "lvgl_ui_test"
MAX_ATTEMPTS = 3


@dataclass
class Result:
    ok: bool = False          # accepted: compiled, flashed if asked, tests passed if they ran
    reply: str = ""           # the accepted Gemini reply (code + spec), for the chat history
    flashed: bool = False
    tests: str = "skipped"    # "passed", "failed", "skipped" or "not_run" (setup problem)
    checks_passed: int = 0
    checks_total: int = 0
    photos: int = 0
    problem: str = ""         # developer text: why the tests could not run
    run_dir: Path = None
    theme: str = "dark"       # colours now on the board
    theme_wanted: str = "dark"  # colours the user asked for (ui_spec.json "theme")
    not_possible: list = None   # [{"item", "why"}] from the independent requirements


def default_generator():
    """The generate() that writes the UI, chosen by GENERATOR_PROVIDER: gemini
    (default) or openai. The photo judge always uses Gemini (see judge.py)."""
    provider = os.environ.get("GENERATOR_PROVIDER", "").strip().lower() or "gemini"
    if provider == "openai":
        return openai_client.generate
    if provider != "gemini":
        raise RuntimeError(f"GENERATOR_PROVIDER must be gemini or openai, not {provider!r}")
    return gemini_client.generate


def find_pio() -> str:
    found = shutil.which("pio") or shutil.which("platformio")
    if found:
        return found
    for rel in ("Scripts/pio.exe", "bin/pio"):
        candidate = Path.home() / ".platformio" / "penv" / rel
        if candidate.exists():
            return str(candidate)
    raise RuntimeError("PlatformIO (pio) not found on PATH or in ~/.platformio/penv")


def compile_ui(pio: str, env: str = PIO_ENV):
    """Returns (ok, error_text)."""
    proc = subprocess.run(
        [pio, "run", "-e", env], cwd=ROOT, capture_output=True, text=True, timeout=600
    )
    if proc.returncode == 0:
        return True, ""
    output = (proc.stdout + proc.stderr).splitlines()
    errors = [line for line in output if "error" in line.lower() and "lv_conf.h" not in line] or output[-20:]
    return False, "\n".join(errors[:25])


FLASH_TRIES = 3  # the ESP32's USB auto-reset sometimes misses the bootloader; a retry fixes it


def flash_ui(pio: str, port: str, env: str = PIO_ENV, log=None):
    cmd = [pio, "run", "-e", env, "-t", "upload"]
    if port:
        cmd += ["--upload-port", port]
    for attempt in range(1, FLASH_TRIES + 1):
        proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, timeout=600)
        output = (proc.stdout + proc.stderr).splitlines()
        if proc.returncode == 0:
            return True, "\n".join(output[-8:])
        # Keep the lines that say why (esptool's "A fatal error occurred: ...").
        reasons = [l for l in output if "fatal error" in l.lower() or "could not open" in l.lower()
                   or "failed to connect" in l.lower()] or output[-8:]
        if log and attempt < FLASH_TRIES:
            log(f"Upload failed (try {attempt}/{FLASH_TRIES}): {reasons[0].strip()}; retrying...")
            time.sleep(2)
    return False, "\n".join(reasons)


def describe_crash(crash, env: str) -> str:
    """Plain text for the AI: what crashed and where, with the backtrace decoded
    into functions and generated_ui.cpp line numbers when possible."""
    lines = crash.report.splitlines()
    text = [lines[0] if lines else "The board crashed."]
    if "EXCVADDR: 0x00000000" in crash.report:
        text.append("It read or wrote memory address 0: a null pointer was used.")
    elf = ROOT / ".pio" / "build" / env / "firmware.elf"
    tools = list((Path.home() / ".platformio" / "packages").glob("toolchain-xtensa-esp32*/bin/*-addr2line*"))
    if crash.addresses and elf.exists() and tools:
        proc = subprocess.run([str(tools[0]), "-pfiaC", "-e", str(elf), *crash.addresses],
                              capture_output=True, text=True, timeout=30)
        frames = [l.strip() for l in proc.stdout.splitlines() if l.strip() and "??" not in l]
        if frames:
            text.append("Call stack, innermost first:")
            for frame in frames[:8]:
                # "0x400d205f: create_button_grid(...) at C:/.../generated_ui.cpp:148" -> keep the useful part
                frame = re.sub(r"^0x[0-9a-f]+:\s*", "", frame)
                frame = re.sub(r" at \S*[/\\]", " at ", frame)
                # Optimised static functions stay mangled: "_ZL18create_button_gridPP..." -> "create_button_grid()"
                mangled = re.match(r"_ZL?(\d+)", frame)
                if mangled:
                    n, start = int(mangled.group(1)), mangled.end()
                    frame = frame[start:start + n] + "()" + frame[frame.find(" at "):]
                text.append("  " + frame)
    elif crash.addresses:
        text.append("Backtrace: " + " ".join(crash.addresses))
    return "\n".join(text)


THEME_HEADER =THEME_FILE.read_text(encoding="utf-8") if THEME_FILE.exists() else "#define UI_LIGHT_THEME 0\n"


def write_theme(light: bool):
    """Sets UI_LIGHT_THEME in ui_theme.h. Only rewrites the file when it changes,
    so an unchanged theme doesn't trigger a recompile."""
    text = re.sub(r"(?m)^#define UI_LIGHT_THEME \d+", f"#define UI_LIGHT_THEME {1 if light else 0}", THEME_HEADER)
    if not THEME_FILE.exists() or THEME_FILE.read_text(encoding="utf-8") != text:
        THEME_FILE.write_text(text, encoding="utf-8")


def switch_to_light(pio, port, env, the_spec, out_dir, log, open_device=None):
    """After every check passed in dark colours: rebuild with the light colours,
    flash, and re-run the exact state checks (no photos). Returns (ok, note). On
    failure the dark build is put back on the board."""
    log("All checks passed in the dark theme; switching to the light theme the user asked for...")
    write_theme(True)
    note = ""
    compiled, errors = compile_ui(pio, env)
    if not compiled:
        note = "the light build did not compile: " + errors
    elif not flash_ui(pio, port, env, log)[0]:
        note = "the light build could not be flashed"
    else:
        try:
            report = run_tests(the_spec, port, out_dir, log, open_device=open_device, use_camera=False)
        except (device_runner.DeviceError, device_runner.BoardCrash, camera.CameraError) as e:
            note = f"the light build could not be re-checked: {e}"
        else:
            if report["passed"]:
                log("Light theme passed the state checks.")
                return True, ""
            note = "state checks failed in the light theme:\n" + spec_runner.describe_failures(report["failures"])
    log(f"Keeping the dark theme: {note}")
    write_theme(False)
    if compile_ui(pio, env)[0]:
        flash_ui(pio, port, env, log)
    return False, note


def build_messages(user_request: str, history=None):
    """Returns (messages, user_text). history is the list of earlier accepted
    (role, text) turns; with history the request is treated as a change to it."""
    if history:
        text = (
            "Change the current interface as follows. Keep everything else the same, "
            "update the tests to match, and return both complete files "
            "(generated_ui.cpp and ui_spec.json) in the usual format:\n" + user_request.strip()
        )
    else:
        text = "Create this user interface:\n" + user_request.strip()
    return list(history or []) + [("user", text)], text


def new_run_dir(user_request: str) -> Path:
    run_dir = RUNS_DIR / time.strftime("%Y-%m-%d_%H%M%S")
    n = 1
    while run_dir.exists():
        n += 1
        run_dir = RUNS_DIR / (time.strftime("%Y-%m-%d_%H%M%S") + f"_{n}")
    run_dir.mkdir(parents=True)
    (run_dir / "prompt.txt").write_text(user_request, encoding="utf-8")
    return run_dir


def earlier_requests(history) -> list:
    """The user's own words from the chat history (build_messages adds a preamble line)."""
    out = []
    for role, text in history or []:
        if role == "user" and isinstance(text, str):
            out.append(text.split("\n", 1)[1] if "\n" in text else text)
    return out


def run_tests(the_spec: dict, port: str, out_dir: Path, log=print, judge=None,
              open_device=None, open_camera=None, use_camera: bool = True, must_see=None) -> dict:
    """Opens the board and (if CAMERA_INDEX is set and use_camera) the camera, then
    runs the spec. Raises device_runner.DeviceError / camera.CameraError on setup problems."""
    open_device = open_device or (lambda: device_runner.Device(port, log=log))
    settings = camera.settings_from_env()
    if not use_camera:
        open_camera = None
    elif open_camera is None and settings:
        open_camera = lambda: camera.Camera(*settings, log=log)
    with contextlib.ExitStack() as stack:
        device = stack.enter_context(open_device())
        cam = stack.enter_context(open_camera()) if open_camera else None
        if cam is None:
            log("State checks only, no photos." if not use_camera
                else "CAMERA_INDEX is not set: running state checks only, no photos.")
        return spec_runner.run_spec(the_spec, device, cam, out_dir, log=log, judge=judge, must_see=must_see)


def run(user_request: str, flash: bool = False, port: str = None, log=print, get_code=None,
        history=None, test: bool = False, judge=None, open_device=None, open_camera=None,
        get_requirements=None) -> Result:
    """get_code(system_prompt, messages) -> str replaces the AI call, judge /
    open_device / open_camera replace the hardware (used for testing).
    get_requirements(requests) -> dict replaces requirements.derive; by default it
    only runs when the AI writes the UI (not for a saved reply).
    test only applies when flash is on: the tests run on the board."""
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    source = "the saved reply" if get_code else (os.environ.get("GENERATOR_PROVIDER") or "gemini")
    get_code = get_code or default_generator()
    pio = find_pio()
    test = test and flash
    env = TEST_ENV if test else PIO_ENV
    run_dir = new_run_dir(user_request)
    log(f"Run folder: {run_dir}")

    backup = TARGET.read_text(encoding="utf-8") if TARGET.exists() else None
    theme_backup = THEME_FILE.read_text(encoding="utf-8") if THEME_FILE.exists() else None
    messages, _ = build_messages(user_request, history)

    # Independent acceptance criteria, before the builder sees anything.
    if get_requirements is None and source != "the saved reply":
        get_requirements = requirements.derive
    req = None
    if get_requirements:
        try:
            req = get_requirements(earlier_requests(history) + [user_request])
        except gemini_client.GeminiError as e:
            log(f"Could not get independent requirements ({e}); continuing without them.")
    if req:
        (run_dir / "requirements.json").write_text(json.dumps(req, indent=2, ensure_ascii=False), encoding="utf-8")
        log(f"Requirements: {len(req['must_see'])} to see, {len(req['must_do'])} to do, "
            f"{len(req['not_possible'])} not possible"
            + "".join(f"\n  not possible: {x['item']} ({x['why']})" for x in req["not_possible"]))
        role, text = messages[-1]
        messages[-1] = (role, text + "\n\n" + requirements.for_builder(req))
    must_see = req["must_see"] if req else None
    not_possible = req["not_possible"] if req else []
    keep_new = False      # True once generated code has been accepted
    board_changed = False  # a failed attempt is on the board and must be replaced
    last_report = None

    try:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            attempt_dir = run_dir / f"attempt_{attempt}"
            attempt_dir.mkdir()
            log(f"Attempt {attempt}/{MAX_ATTEMPTS}: asking {source}...")
            reply = get_code(system_prompt, messages)

            code, spec_text, problems = spec_module.split_reply(reply)
            (attempt_dir / "generated_ui.cpp").write_text(code, encoding="utf-8")
            (attempt_dir / "ui_spec.json").write_text(spec_text, encoding="utf-8")
            the_spec = None
            if not problems:
                the_spec, spec_problems = spec_module.parse(spec_text)
                problems = spec_problems
                if the_spec is not None and not spec_problems:
                    problems += spec_module.check_against_code(the_spec, code)
            problems = validator.validate(code) + problems
            accepted_reply = spec_module.join_reply(code, spec_text)

            if problems:
                REJECTED_FILE.write_text(code, encoding="utf-8")
                log(f"Rejected by rule check ({len(code)} chars, saved to {REJECTED_FILE.name}):\n  - "
                    + "\n  - ".join(problems))
                feedback = "Your reply broke these rules:\n- " + "\n- ".join(problems)
            else:
                TARGET.write_text(code, encoding="utf-8")
                wanted = the_spec.get("theme", "dark")
                # Tests and photos always run in dark colours; without tests the
                # wanted theme goes straight on.
                write_theme(wanted == "light" and not test)
                log(f"Rule check passed ({len(the_spec['tests'])} tests, {wanted} theme wanted). Compiling...")
                compiled, errors = compile_ui(pio, env)
                if not compiled:
                    log("Compile failed:\n" + errors)
                    feedback = "Your code failed to compile:\n" + errors
                else:
                    log("Compiled successfully.")
                    themes = {"theme": "light" if wanted == "light" and not test else "dark",
                              "theme_wanted": wanted, "not_possible": not_possible}
                    if not flash:
                        keep_new = True
                        return Result(ok=True, reply=accepted_reply, run_dir=run_dir, **themes)
                    log("Flashing...")
                    flashed, tail = flash_ui(pio, port, env, log)
                    log(tail)
                    if not flashed:
                        keep_new = True  # compiled; the board just wasn't reachable
                        return Result(ok=False, reply=accepted_reply, run_dir=run_dir, **themes)
                    log("Flashed.")
                    if not test:
                        keep_new = True
                        return Result(ok=True, reply=accepted_reply, flashed=True, run_dir=run_dir, **themes)

                    board_changed = True
                    log("Running the tests on the board...")
                    try:
                        report = run_tests(the_spec, port, attempt_dir / "tests", log, judge,
                                           open_device, open_camera, must_see=must_see)
                    except device_runner.BoardCrash as crash:
                        # A crash is the UI's fault: send it back like any failed check.
                        details = describe_crash(crash, env)
                        log("The UI crashed the board:\n" + details)
                        (attempt_dir / "crash.txt").write_text(crash.report + "\n\n" + details, encoding="utf-8")
                        feedback = (
                            "The UI compiled, but it CRASHED the ESP32 when it ran, so the screen "
                            "stayed blank and the board kept rebooting:\n" + details +
                            "\nFind and fix the cause in generated_ui.cpp (check every pointer and "
                            "array index on that path)."
                        )
                        messages.append(("model", accepted_reply))
                        messages.append(("user", feedback + "\nReturn both complete files again, in the same format."))
                        continue
                    except (device_runner.DeviceError, camera.CameraError) as e:
                        log(f"Tests could not run: {e}")
                        keep_new, board_changed = True, False  # stays dark: it wasn't checked
                        return Result(ok=True, reply=accepted_reply, flashed=True, tests="not_run",
                                      problem=str(e), run_dir=run_dir, **themes)
                    last_report = report
                    log(f"Tests: {report['checks_passed']}/{report['checks_total']} checks passed, "
                        f"{report['photos']} photos.")
                    if report["passed"]:
                        keep_new, board_changed = True, False
                        if wanted == "light":
                            switched, _ = switch_to_light(pio, port, env, the_spec,
                                                          attempt_dir / "tests_light", log, open_device)
                            themes["theme"] = "light" if switched else "dark"
                        return Result(ok=True, reply=accepted_reply, flashed=True, tests="passed",
                                      checks_passed=report["checks_passed"],
                                      checks_total=report["checks_total"],
                                      photos=report["photos"], run_dir=run_dir, **themes)
                    failures = spec_runner.describe_failures(report["failures"])
                    log("Failed checks:\n" + failures)
                    feedback = (
                        "The UI compiled and was tested on the real device. These checks failed:\n"
                        + failures + "\nFix generated_ui.cpp. Only change a test if it contradicts "
                        "what the user asked for; never weaken a test just to make it pass."
                    )

            messages.append(("model", accepted_reply))
            messages.append(("user", feedback + "\nReturn both complete files again, in the same format."))

        log(f"Giving up after {MAX_ATTEMPTS} attempts; previous UI restored.")
        result = Result(ok=False, run_dir=run_dir, not_possible=not_possible)
        if last_report:
            result.tests = "failed"
            result.checks_passed = last_report["checks_passed"]
            result.checks_total = last_report["checks_total"]
        return result
    finally:
        if not keep_new and backup is not None:
            TARGET.write_text(backup, encoding="utf-8")
            if theme_backup is not None:
                THEME_FILE.write_text(theme_backup, encoding="utf-8")
            if board_changed:
                log("Putting the previous UI back on the board...")
                compiled, _ = compile_ui(pio, env)
                restored = compiled and flash_ui(pio, port, env, log)[0]
                log("Previous UI restored on the board." if restored
                    else "Could not restore the previous UI on the board.")
        (run_dir / "outcome.json").write_text(json.dumps({"accepted": keep_new}), encoding="utf-8")

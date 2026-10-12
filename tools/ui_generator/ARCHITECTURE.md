# UI Generator: Automated Test and Visual Verification Architecture

> Status (2026-10-11): phases 1, 3 and 5 are implemented: Gemini writes
> `ui_spec.json`, the board runs it over serial (`lvgl_ui/test_hooks.cpp`,
> `device_runner.py`, `spec_runner.py`), and the camera and vision judge check each
> test (`camera.py`, `judge.py`). **Not built yet:** the PC host simulation (§5),
> the `SHOT` framebuffer dump and photo-vs-framebuffer comparison (§6.1, §7.3),
> fiducial markers and rectification (§7.1, a plain crop for now), re-judging
> with fresh photos (§8) and `report.html` (§10). The step `drag` is implemented
> as described; `tap` uses the widget centre.

## 1. Goal

Turn the current flow

```
prompt → Gemini → validate → compile → flash
```

into a closed loop that, without a human in it, also:

1. Has Gemini write **tests** for every UI it generates.
2. Runs those tests: first in a PC simulation, then on the real ESP32.
3. Uses a **camera** to photograph the real screen during each test (one or more
   photos per test) and has Gemini check that the UI is rendered correctly.
4. Feeds any failure back to Gemini to fix, with a bounded retry limit, and
   rolls back to the last good UI if every attempt fails.

## 2. Principles

| Principle | Why |
|---|---|
| **Gemini writes data, not test code.** Tests are a declarative JSON spec run by a fixed, human-written runner. | Same trust boundary as today: Gemini never writes code that drives hardware. Specs are easy to validate. |
| **Cheapest check first.** Rules → compile → simulation → device → camera. | Most failures are caught in seconds and for free. The camera and vision calls only run on UIs that already passed everything else. |
| **Deterministic checks before AI judgement.** Widget state and framebuffer comparison are exact. Gemini only judges what they cannot (looks right? matches intent?). | Fewer hallucinated verdicts and cheaper runs. |
| **The judge is separate from the generator.** The vision judge gets a fresh context, with no access to the generator conversation. | Stops the model rationalising its own output. |
| **Every run is recorded.** Prompt, code, spec, photos, framebuffers and verdicts are saved per run. | Auditing the judge, debugging the rig, reproducibility. |
| **Test hooks never ship.** Device test commands are compiled only with `TEST_HOOKS`. | No remote-control surface in production firmware. |

## 3. Pipeline overview

```
user prompt
   │
   ▼
[1 Generate]   Gemini → generated_ui.cpp + ui_spec.json
   │
[2 Static]     validator.py (existing) + spec schema/budget check
   │
[3 Compile]    pio run -e lvgl_ui (existing)
   │
[4 Host sim]   same UI code built for PC (LVGL headless)
   │           → widget tree + screenshot + run spec tests
   │
[5 Flash]      pio run -e lvgl_ui_test -t upload  (TEST_HOOKS on)
   │
[6 Device]     serial test runner: TREE / TAP / DRAG / WAIT_IDLE / SHOT
   │
[7 Camera]     per-test captures → rectify → compare to framebuffer
   │
[8 Judge]      Gemini vision, one call per test with all its captures
   │
   ▼
all pass → keep UI, show plain-language result to user
any fail → structured failure report → back to [1]
           (max N attempts, then restore last good generated_ui.cpp)
```

Each stage returns the same result shape (see §9), so the orchestrator only has
to decide whether to continue, retry or roll back.

## 4. Generation output

Gemini returns **two artefacts** per attempt:

1. `lvgl_ui/generated_ui.cpp`: as today, constrained by `system_prompt.txt`
   and `validator.py`.
2. `ui_spec.json`: describes the widgets, the tests and what each photo should
   show.

### 4.1 Widget IDs (contract extension)

Tests refer to widgets by a stable string ID. Extend the fixed contract in
`lvgl_ui/generated_ui.h`:

```cpp
// Registers a widget under a stable ID so tests can find it.
// Implemented in fixed code (test_hooks.cpp / no-op without TEST_HOOKS).
void ui_register(lv_obj_t *obj, const char *id);
```

The validator enforces that every ID used in `ui_spec.json` is registered
in `generated_ui.cpp`, and the reverse for interactive widgets.

### 4.2 `ui_spec.json` example

```json
{
  "intent": "light switch with brightness slider",
  "widgets": [
    {"id": "sw_light",  "type": "switch", "visible": true},
    {"id": "sl_bright", "type": "slider", "range": [0, 100]},
    {"id": "lbl_value", "type": "label"}
  ],
  "tests": [
    {
      "name": "toggle light on",
      "steps": [
        {"capture": {"label": "before", "expect_visual": ["switch is OFF (grey)"]}},
        {"tap": "sw_light"},
        {"expect": {"sw_light": {"checked": true}}},
        {"capture": {"label": "after", "expect_visual": ["switch is ON (coloured)"]}}
      ]
    },
    {
      "name": "brightness sweep",
      "steps": [
        {"drag": ["sl_bright", 0]},
        {"expect": {"lbl_value": {"text": "0%"}}},
        {"capture": {"label": "min", "expect_visual": ["label reads 0%"]}},
        {"drag": ["sl_bright", 100]},
        {"expect": {"lbl_value": {"text": "100%"}}},
        {"capture": {"label": "max", "expect_visual": ["label reads 100%"]}}
      ]
    }
  ],
  "visual_rubric": [
    "All widgets are fully visible and not clipped",
    "Text is readable",
    "Nothing overlaps"
  ]
}
```

### 4.3 Step types

| Step | Meaning |
|---|---|
| `tap: id` | Inject a click at the widget's centre |
| `drag: [id, value]` | Move a slider/arc to a value |
| `wait_ms: n` | Explicit wait (rarely needed, see `WAIT_IDLE`) |
| `expect: {id: {prop: value}}` | Exact widget-state assertion (checked, value, text, visible, hidden) |
| `capture: {label, expect_visual[]}` | Take photo(s) now. `expect_visual` lists what must be visible in this photo. |

There is always one implicit `initial` capture after boot, judged against
`visual_rubric` and `intent`.

### 4.4 Spec validation and budget

`spec_schema.json` plus checks in `validator.py`:

- JSON schema valid; only known step types and widget properties.
- All IDs exist (§4.1).
- **Budget:** ≤ 10 tests, ≤ 6 captures per test, ≤ 30 captures per run.
- Every test has at least one `expect` or `capture` (no empty tests).

## 5. Host simulation (stage 4)

Build the same `generated_ui.cpp` against LVGL on the PC, headless (render to
an in-memory buffer at 320×240, same colour depth and same `lv_conf.h`).

- Runs the full spec: `tap`/`drag` use the same injection code as the device.
  `expect` reads the widget tree. `capture` saves the buffer as PNG (no camera).
- Adds geometry checks the validator can't do statically: widgets out of
  screen bounds, overlapping interactive widgets, clipped labels.
- Runs in seconds, so most retries with Gemini happen here without touching
  the board.

Implementation: a small CMake or `pio` `native` env (`env:lvgl_sim`) with a
fixed `sim_main.cpp` that loads the UI and speaks the **same command protocol
as the device over stdin/stdout**. `device_runner.py` can then drive either one.

## 6. On-device test runner (stages 5–6)

### 6.1 Firmware side: `lvgl_ui/test_hooks.cpp` (fixed, human-written)

Compiled only when `TEST_HOOKS` is defined (new env `lvgl_ui_test`). Adds a
line-based serial protocol:

| Command | Reply |
|---|---|
| `PING` | `OK` |
| `TREE` | JSON: every registered widget with id, type, bounds, text, value, state flags, visibility |
| `TAP x y` / `TAP_ID id` | Injects press/release through a virtual LVGL input device → `OK` |
| `DRAG id value` | Injects a press-move-release → `OK` |
| `WAIT_IDLE` | Replies `IDLE` once LVGL has no pending invalidations or animations (timeout → `TIMEOUT`) |
| `SHOT` | Framebuffer dump (RGB565, chunked, with CRC) |
| `RESET_UI` | `generated_ui_destroy(); generated_ui_create();` → `OK` (resets between tests) |

Note: `SHOT` needs either a full-frame buffer or re-rendering into a temporary
buffer in strips. With the ESP32's RAM, strip re-render is the expected choice.

### 6.2 PC side: `device_runner.py`

- Opens the serial port (`UPLOAD_PORT` from `.env`), executes the spec steps,
  and collects `TREE`/`SHOT` results.
- Calls `RESET_UI` before each test so tests are independent.
- Same interface for the simulator (§5).

### 6.3 Coverage limit

Injected input bypasses the physical XPT2046 touch panel. Physical touch is
covered by the existing Unity hardware tests in `test/`, not by this pipeline.

## 7. Camera stage (stage 7): multiple captures per test

### 7.1 Rig

- Camera fixed in front of the screen (mount; must not move between runs).
- Consistent lighting, with **locked exposure / white balance / focus** to avoid
  glare and flicker.
- **Screen localisation:** four fiducial markers (ArUco on the bezel, or four
  corner dots drawn by the firmware on a `CALIBRATE` command) let us find and
  rectify the screen in every photo.
- One-time `calibrate.py` stores the homography and colour reference.

### 7.2 Per-capture flow

```
for each test:
    RESET_UI
    for each step:
        action  → device_runner: TAP / DRAG
        expect  → device_runner: TREE → exact compare        (fail fast)
        capture →
            1. WAIT_IDLE                  (screen fully redrawn)
            2. TREE                       (widget state at this instant)
            3. SHOT                       (what LVGL drew)
            4. camera burst: 3 frames
            5. keep sharpest (Laplacian variance); if all blurry, retry once
            6. rectify to 320×240 using the calibration
            7. compare photo vs SHOT      (deterministic, see 7.3)
            8. store {label, tree, shot, photo, diff}
    → send all captures of this test to the judge (§8)
```

### 7.3 Photo vs. framebuffer comparison (no AI)

- Coarse colour-grid diff (e.g. 16×12 cells) plus SSIM on the rectified photo
  against `SHOT`.
- Large mismatch → **hardware/rendering failure** (display wiring, driver,
  colour order, blank screen). It is reported as such and **not** sent back to
  Gemini as a UI bug.
- Thresholds are tuned once on the rig and stored in `camera/config.json`.

### 7.4 Why multiple captures

- State changes ("did the switch turn on?") can only be verified by comparing
  photos taken before and after the action.
- Each photo has its own `expect_visual`, so a failure points to a specific step
  rather than "the UI is wrong".
- The burst per capture absorbs camera noise and refresh timing.

## 8. Vision judge (stage 8)

- **One Gemini call per test**, with all of that test's photos in order and
  their labels, plus the `expect_visual` lines per photo, the `visual_rubric`
  and the user's `intent`.
- Separate prompt (`prompts/judge.txt`) and fresh context: it never sees the
  generator's conversation or code. A different model from the generator is
  optional but recommended.
- **Strict JSON output**, one verdict per criterion:

```json
{
  "test": "toggle light on",
  "captures": [
    {"label": "before", "criteria": [
      {"text": "switch is OFF (grey)", "pass": true,  "reason": "switch track is grey"}
    ]},
    {"label": "after", "criteria": [
      {"text": "switch is ON (coloured)", "pass": false, "reason": "switch still grey, knob on left"}
    ]}
  ]
}
```

- The judge may only answer the listed criteria. Free-form opinions are ignored.
- On a judge failure, re-judge once with fresh photos before declaring failure
  (guards against a single bad frame).

## 9. Orchestrator and feedback loop

`pipeline.py` *(existing)* becomes a small state machine over the stages.

### 9.1 Common stage result

```json
{
  "stage": "device",
  "passed": false,
  "failures": [
    {"test": "toggle light on", "step": 3, "label": "after",
     "id": "sw_light", "expected": "checked=true", "actual": "checked=false",
     "artifacts": ["runs/.../toggle_light_on/02_after/photo.jpg"]}
  ]
}
```

### 9.2 Retry policy

| Failure kind | Action |
|---|---|
| Rule / schema / compile | Send errors to the generator, retry (existing behaviour) |
| Sim or device `expect` failure | Send the failing steps and the tree to the generator, retry |
| Judge failure | Send the criterion, reason and photo to the generator, retry |
| Photo-vs-framebuffer mismatch | **Do not** retry with Gemini. Report a hardware/rig problem to the user. |
| Attempts exhausted (default 3) | Restore the last good `generated_ui.cpp`, report to the user |

### 9.3 User-facing output

As today, the chat shows plain language only ("Done — your switch and slider
are on the screen; all 2 checks passed"). Details live in the run folder and
`report.html`.

## 10. Run artefacts

```
runs/2026-10-11_1432/
  prompt.txt
  attempt_1/
    generated_ui.cpp  ui_spec.json  result.json
  attempt_2/ ...
  final/
    initial/                   photo.jpg  shot.png  tree.json  diff.json
    toggle_light_on/
      01_before/               photo.jpg  shot.png  tree.json  diff.json
      02_after/                ...
      verdict.json
    brightness_sweep/ ...
  report.html                  camera | framebuffer | verdict, side by side
```

`runs/` is git-ignored.

## 11. Planned file layout

```
lvgl_ui/
  main.cpp               (existing, fixed)
  generated_ui.h         (existing, + ui_register)
  generated_ui.cpp       (existing, Gemini-written)
  test_hooks.cpp         NEW, fixed, only with TEST_HOOKS
tools/ui_generator/
  server.py              (existing) chat UI
  pipeline.py            (existing) → stage orchestrator
  validator.py           (existing) + spec checks
  gemini_client.py       (existing) + image input for judge
  system_prompt.txt      (existing) → prompts/generator.txt (+ spec rules)
  prompts/judge.txt      NEW
  spec_schema.json       NEW
  device_runner.py       NEW: serial/stdin protocol client
  host_sim/              NEW: PC build of LVGL + UI (sim_main.cpp)
  camera/
    calibrate.py         NEW
    capture.py           NEW: burst + sharpness pick
    rectify.py           NEW
    compare.py           NEW: grid diff + SSIM vs SHOT
    config.json          NEW: camera index, exposure, thresholds
  judge.py               NEW
  report.py              NEW: report.html
platformio.ini           + env:lvgl_ui_test (TEST_HOOKS), env:lvgl_sim
```

New Python dependencies: `pyserial`, `opencv-python`, `numpy`,
`scikit-image` (SSIM). Today the tool is standard library only. This is the
main dependency change to accept.

## 12. Implementation phases

| Phase | Deliverable | Needs hardware? |
|---|---|---|
| 1 | Widget IDs, `ui_spec.json` schema, generator emits spec, validator checks it | No |
| 2 | Host simulation + spec runner; retries driven by sim results | No |
| 3 | `test_hooks.cpp` + `device_runner.py`; `expect` steps on device | Board |
| 4 | Camera rig, calibration, capture/rectify, photo-vs-`SHOT` comparison (no Gemini) | Board + camera |
| 5 | Vision judge, per-test multi-capture verdicts, full loop | Board + camera |
| 6 | `report.html`, run history, threshold tuning | — |

Each phase is useful on its own. Phase 2 alone should catch most generated-UI
bugs.

## 13. Risks and open questions

| Risk | Mitigation |
|---|---|
| Camera flakiness (glare, exposure, moiré) fails good UIs | Fixed mount, locked exposure, burst + sharpest frame, one re-capture, tuned thresholds |
| Judge hallucination | Criteria-only JSON, fresh context, deterministic checks first, every verdict logged with its photo |
| `SHOT` too slow or memory-heavy on ESP32 | Strip re-render, or downsample the dump. Compare at coarse grid resolution only. |
| Gemini writes weak or trivial tests | Validator minimums (at least one action test per interactive widget, at least one capture per test) |
| Cost / latency of vision calls | One call per test, camera stage only after all cheaper stages pass, capture budget |
| Injected touch ≠ physical touch | Out of scope here; covered by the existing Unity hardware tests |

**Open questions for review**

1. Which camera (USB webcam, phone, ESP32-CAM), and how does it connect to the PC?
2. Is adding OpenCV/numpy/pyserial to the tool acceptable?
3. Same Gemini model as generator and judge, or a separate judge model?
4. Should the chat UI show the photos, or only plain-language results?
5. Default limits: 3 attempts, 6 captures per test, 30 per run. Acceptable?

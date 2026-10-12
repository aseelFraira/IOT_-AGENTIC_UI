# UI generator

Type which interface you want. Gemini writes `lvgl_ui/generated_ui.cpp` **and the
tests for it** (`ui_spec.json`). The tool checks both against the rules, compiles,
flashes the board, runs the tests on the real screen, photographs it with a
camera and has Gemini check the photos. Failed checks go back to Gemini to fix.

The ESP32 cannot compile C++ itself, so this runs on the PC. Design notes:
[ARCHITECTURE.md](ARCHITECTURE.md).

## Setup (once)

Copy `.env.example` (project root) to `.env` and put your key in it:

```
GEMINI_API_KEY=<your key>
# GEMINI_MODEL=gemini-2.5-flash    (optional, this is the default)
# UPLOAD_PORT=COM4                 (optional; also used for the test commands)
```

`.env` is git-ignored; never commit a key. A variable set in the terminal
(`$env:GEMINI_API_KEY = "..."`) overrides the file.

Python 3.9+. Generating and compiling need only the standard library; the board
tests and the camera need `pip install -r tools/ui_generator/requirements.txt`
(OpenCV and pyserial).

### Camera

1. Point the camera at the screen; keep it fixed, avoid glare.
2. `python tools/ui_generator/camera_check.py` saves one picture per camera in
   `tools/ui_generator/runs/camera_check/`. Pick the one that shows the screen and
   set `CAMERA_INDEX=<n>` in `.env`.
3. Optional: crop to just the screen with `CAMERA_CROP=x,y,w,h` (pixels in that
   picture) and/or `CAMERA_ROTATE=90`. Check with
   `python tools/ui_generator/camera_check.py --index <n>`.

Close Zoom/Teams/the Camera app first; a camera in use gives no picture.
Without `CAMERA_INDEX` the tests still run, just without photos.

## Use

Web UI: `python tools/ui_generator/server.py`, then open http://127.0.0.1:8765

The page is a chat: describe a screen, then keep asking for changes ("make the
button bigger"). The conversation lives on the server, so a refresh keeps it;
"Start over" begins a new one. The user only sees plain-language messages. Code,
rule violations, compiler output and test results appear only in the terminal
running the server.

It flashes the board and runs the tests after each successful build. Start it
with `TESTS=0` to flash without testing, or `FLASH=0` to only compile. If port
8765 is already used by an old server, stop that one first.

CLI:

```
python tools/ui_generator/generate.py "a light switch with a brightness slider" --test
python tools/ui_generator/generate.py "..." --flash          # no tests
python tools/ui_generator/generate.py "..." --code-file reply.txt --test   # no Gemini
```

## How it works

1. `system_prompt.txt` (the rules, the test format) is sent to Gemini with every
   request. Gemini replies with two files after marker lines:
   `===generated_ui.cpp===` and `===ui_spec.json===`.
2. `validator.py` rejects code that breaks the rules; `spec.py` rejects malformed
   tests or tests over budget (10 tests, 6 photos per test, 30 in total).
3. `pio run -e lvgl_ui_test` compiles it, with the serial test hooks
   (`lvgl_ui/test_hooks.cpp`). Without tests the plain `lvgl_ui` env is used.
4. The board is flashed, and `spec_runner.py` runs the tests over serial
   (`device_runner.py`): every widget the tests use must be registered with
   `ui_register()`, every widget must fit on screen, and each test starts from a
   fresh UI, taps/drags widgets and checks their exact state.
5. At each `capture` step `camera.py` photographs the screen (sharpest of a short
   burst). `judge.py` sends each test's photos to Gemini in a separate, fresh
   conversation and gets a pass/fail per sentence in `expect_visual`.
6. On any rule, compile or test failure, the errors go back to Gemini, up to 3
   attempts. If every attempt fails, the previous `generated_ui.cpp` is restored
   and put back on the board.
7. If the board or camera doesn't respond, that is a setup problem, not a UI bug:
   the UI is kept and the chat says the checks could not run.

Every run is saved in `tools/ui_generator/runs/<timestamp>/` (git-ignored): the
prompt, each attempt's code and spec, widget trees, photos and the judge's verdicts.

Firmware split: `lvgl_ui/main.cpp` and `lvgl_ui/test_hooks.cpp` are fixed. Only
`lvgl_ui/generated_ui.cpp` is ever rewritten. `generated_ui.h` is the fixed contract.

## Test protocol (lvgl_ui_test build only)

Line-based over the USB serial port at 115200 baud; replies start with `@@`:
`PING`, `TREE`, `TAP_ID <id>`, `TAP <x> <y>`, `DRAG <id> <value>`, `WAIT_IDLE`,
`RESET_UI`. Taps go through a virtual LVGL input device, not the touch panel
(the panel itself is covered by the Unity tests in `test/`). Never ship this
build: it lets the serial port press buttons.

## Limits

- No photo-vs-framebuffer comparison yet, so a display wiring fault shows up as a
  failed visual check rather than a hardware error.
- Bounds are only checked for literal numbers before compiling; on the board,
  registered widgets are checked against the screen size.
- The approved widget list is in `validator.py` (`APPROVED_WIDGETS`) and in
  `system_prompt.txt`; a test checks they agree.

## Tests

`cd tools/ui_generator && python -m unittest -v` (no board, camera or key needed).

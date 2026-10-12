"""Static checks on Gemini's generated_ui.cpp before it is compiled or flashed.

These enforce the rules in system_prompt.txt. They are a safety net, not proof:
the compiler is the final gate, and widget bounds are only checked for literal
numbers (computed or alignment-based positions cannot be evaluated here).
"""

import re

SCREEN_W = 240
SCREEN_H = 320

APPROVED_WIDGETS = {
    "obj", "label", "btn", "slider", "switch", "checkbox", "bar", "arc", "led",
}

# (regex, message). Matched against the code with comments and strings removed.
FORBIDDEN = [
    (r"\bTFT_eSPI\b|\btft\s*[.\-]", "uses TFT_eSPI / tft directly"),
    (r"\blv_init\b|\blv_deinit\b", "initialises LVGL"),
    (r"\blv_disp_(drv|draw_buf)_\w+|\blv_indev_drv_\w+", "initialises the display or touch driver"),
    (r"\blv_tick_inc\b|\blv_timer_handler\b", "drives the LVGL loop"),
    (r"\b(void|int)\s+(setup|loop)\s*\(", "defines setup() or loop()"),
    (r"\b(pinMode|digitalWrite|digitalRead|analogRead|analogWrite|ledcWrite|attachInterrupt)\s*\(",
     "touches hardware pins"),
    (r"\b(SPI|Wire|Serial\d?|WiFi|HTTPClient|Preferences|EEPROM|SD|SPIFFS|LittleFS)\b",
     "uses a hardware or network library"),
    (r"\bdelay\s*\(|\bdelayMicroseconds\s*\(|\bmillis\s*\(", "uses Arduino timing functions"),
    (r"\bLV_ANIM_ON\b|\blv_anim_\w+", "uses animations"),
    (r"\blv_timer_create\b", "creates timers"),
    (r"\bmalloc\s*\(|\bcalloc\s*\(|\brealloc\s*\(|\bnew\s+\w", "allocates memory outside LVGL"),
    (r"\blv_font_(?!montserrat_14\b)\w+", "uses a font other than lv_font_montserrat_14"),
    (r"\bui_register\s*\([^;{]*\)\s*\{", "defines ui_register (the firmware provides it; only call it)"),
    (r"\blvgl_pump\b|\btest_hooks_\w+", "uses the test harness"),
    (r"#\s*(define|undef)\s+UI_LIGHT_THEME\b", "defines UI_LIGHT_THEME (the tool sets it in ui_theme.h)"),
]

FENCE_RE = re.compile(r"^```[a-zA-Z+]*\s*\n(.*?)\n```\s*$", re.DOTALL)


def strip_fences(text: str) -> str:
    """Gemini sometimes wraps code in a markdown fence despite being told not to."""
    text = text.strip()
    m = FENCE_RE.match(text)
    if m:
        return m.group(1).strip() + "\n"
    return text + "\n"


# One pass, left to right, so text inside a string or char literal (for example the
# calculator operator list "/*-+" or the character '"') is never mistaken for a comment.
_TOKEN_RE = re.compile(
    r'"(?:\\.|[^"\\\n])*"'      # string literal
    r"|'(?:\\.|[^'\\\n])*'"     # char literal
    r"|//[^\n]*"                # line comment
    r"|/\*.*?\*/",              # block comment
    re.DOTALL,
)


def _strip_comments_and_strings(code: str) -> str:
    def blank(m):
        text = m.group(0)
        if text[0] == '"':
            return '""'
        if text[0] == "'":
            return "''"
        return " "

    return _TOKEN_RE.sub(blank, code)


def _int_args(call_args: str):
    """Literal integer arguments of a call, or None for a non-literal argument."""
    out = []
    for arg in call_args.split(","):
        arg = arg.strip()
        if re.fullmatch(r"-?\d+", arg):
            out.append(int(arg))
        else:
            out.append(None)
    return out


def _check_bounds(code: str):
    problems = []
    for m in re.finditer(r"\blv_obj_set_(pos|size|x|y|width|height)\s*\(([^;]*)\)\s*;", code):
        kind = m.group(1)
        args = _int_args(m.group(2))[1:]  # first arg is the object
        if kind == "pos" and len(args) == 2:
            x, y = args
            if x is not None and not 0 <= x < SCREEN_W:
                problems.append(f"lv_obj_set_pos x={x} is outside 0..{SCREEN_W - 1}")
            if y is not None and not 0 <= y < SCREEN_H:
                problems.append(f"lv_obj_set_pos y={y} is outside 0..{SCREEN_H - 1}")
        elif kind == "size" and len(args) == 2:
            w, h = args
            if w is not None and w > SCREEN_W:
                problems.append(f"lv_obj_set_size width={w} is wider than the {SCREEN_W}px screen")
            if h is not None and h > SCREEN_H:
                problems.append(f"lv_obj_set_size height={h} is taller than the {SCREEN_H}px screen")
        elif kind in ("x", "width") and len(args) == 1 and args[0] is not None:
            if args[0] > SCREEN_W or (kind == "x" and args[0] < 0):
                problems.append(f"lv_obj_set_{kind}={args[0]} is outside the screen width")
        elif kind in ("y", "height") and len(args) == 1 and args[0] is not None:
            if args[0] > SCREEN_H or (kind == "y" and args[0] < 0):
                problems.append(f"lv_obj_set_{kind}={args[0]} is outside the screen height")
    return problems


def validate(code: str):
    """Returns a list of human-readable problems. Empty list means it passed."""
    problems = []
    if not code.strip():
        return ["output is empty"]
    if "```" in code:
        problems.append("contains a markdown code fence; output must be raw C++ only")

    # Includes must be checked on the raw text: the include path is in quotes.
    if not re.search(r'#\s*include\s*[<"]lvgl\.h[>"]', code):
        problems.append("missing #include <lvgl.h>")
    if not re.search(r'#\s*include\s*"generated_ui\.h"', code):
        problems.append('missing #include "generated_ui.h"')

    clean = _strip_comments_and_strings(code)

    for name in ("generated_ui_create", "generated_ui_destroy"):
        if not re.search(rf"\bvoid\s+{name}\s*\(\s*(void)?\s*\)\s*\{{", clean):
            problems.append(f"does not define void {name}(void)")

    for pattern, message in FORBIDDEN:
        if re.search(pattern, clean):
            problems.append(message)

    for m in re.finditer(r"\blv_(\w+?)_create\s*\(", clean):
        widget = m.group(1)
        if widget not in APPROVED_WIDGETS:
            problems.append(f"uses unapproved widget lv_{widget}_create")

    if "lv_obj_clean" not in clean:
        problems.append("generated_ui_destroy() must call lv_obj_clean(lv_scr_act())")

    problems.extend(_check_bounds(clean))

    seen = set()
    return [p for p in problems if not (p in seen or seen.add(p))]

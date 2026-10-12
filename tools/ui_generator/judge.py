"""Vision judge: Gemini looks at the camera photos of one test and answers each
expect_visual criterion.

It runs in a fresh context with its own prompt (judge_prompt.txt): it never sees
the generator's conversation or the code, so it can't rationalise them.

Its account and model are separate from the generator's:
    GEMINI_JUDGE_API_KEY         its own key (e.g. a paid one); falls back to GEMINI_API_KEY
    GEMINI_JUDGE_MODEL           its model; falls back to GEMINI_MODEL
    GEMINI_JUDGE_FALLBACK_MODEL  optional second model on the judge key. With a
                                 separate key there is none by default, so the
                                 judge never silently switches to another model.

Only the listed criteria count; anything else the model says is ignored.
"""

import json
import os
from pathlib import Path

import gemini_client
from camera import CameraError

PROMPT_FILE = Path(__file__).with_name("judge_prompt.txt")

# Checked on the first photo of every run, in addition to Gemini's own rubric.
DEFAULT_RUBRIC = [
    "No widget or text is cut off at the edges of the display",
    "No text overlaps other text or another widget",
    "All text on the display is readable",
]


def _content(test_name: str, intent: str, captures):
    parts = [{"text": f"Test: {test_name}\nThe screen is meant to show: {intent}\n"}]
    for n, cap in enumerate(captures, 1):
        criteria = "\n".join(f"  {i}. {c}" for i, c in enumerate(cap["expect_visual"], 1))
        parts.append({"text": f"\nPhoto {n}, label \"{cap['label']}\", taken after: {cap['after']}\n"
                              f"Criteria:\n{criteria}\n"})
        parts.append(gemini_client.image_part(Path(cap["photo"]).read_bytes()))
    return parts


def _strip_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else ""
        text = text.rsplit("```", 1)[0]
    return text


def _parse(reply: str):
    data = json.loads(_strip_fence(reply))
    if not isinstance(data, dict) or not isinstance(data.get("captures"), list):
        raise ValueError("missing captures list")
    return data


def call_settings() -> dict:
    """Keyword arguments for gemini_client.generate: the judge's key, model and fallback."""
    separate_key = bool(os.environ.get("GEMINI_JUDGE_API_KEY"))
    fallback = os.environ.get("GEMINI_JUDGE_FALLBACK_MODEL")
    if fallback is None and separate_key:
        fallback = ""  # no silent switch away from the model you pay for
    return {
        "key_var": "GEMINI_JUDGE_API_KEY" if separate_key else "GEMINI_API_KEY",
        "model": os.environ.get("GEMINI_JUDGE_MODEL") or None,
        "fallback_model": fallback,
    }


def judge_test(test_name: str, intent: str, captures, generate=None, check_framing: bool = False):
    """captures: list of {label, photo, after, expect_visual}. Returns
    (failures, verdict). Raises CameraError if the camera can't see the display,
    and with check_framing also if it is cut off or not upright: those are rig
    problems, and must not be blamed on the UI."""
    generate = generate or gemini_client.generate
    system_prompt = PROMPT_FILE.read_text(encoding="utf-8")
    messages = [("user", _content(test_name, intent, captures))]
    settings = call_settings()

    data, last_error = None, None
    for _ in range(2):  # one retry if the reply isn't the JSON we asked for
        reply = generate(system_prompt, messages, temperature=0.0, json_output=True, **settings)
        try:
            data = _parse(reply)
            break
        except ValueError as e:
            last_error = e
    if data is None:
        raise gemini_client.GeminiError(f"judge reply was not valid JSON: {last_error}")

    answers = {a.get("label"): a for a in data["captures"] if isinstance(a, dict)}
    failures, verdict = [], {"test": test_name, "captures": []}
    for cap in captures:
        answer = answers.get(cap["label"], {})
        if answer.get("screen_visible") is False:
            raise CameraError(f"The camera can't see the display (test {test_name!r}, photo "
                              f"{cap['label']!r}). Point it at the screen or fix CAMERA_CROP.")
        if check_framing and answer.get("fully_in_frame") is False:
            raise CameraError(f"Part of the display is outside the photo ({cap['photo']}). Move the "
                              "camera back or fix CAMERA_CROP; check with camera_check.py --index N.")
        if check_framing and answer.get("upright") is False:
            raise CameraError(f"The display is sideways or upside down in the photo ({cap['photo']}). "
                              "Fix CAMERA_ROTATE; check with camera_check.py --index N.")
        given = answer.get("criteria") if isinstance(answer.get("criteria"), list) else []
        results = []
        for i, criterion in enumerate(cap["expect_visual"]):
            got = given[i] if i < len(given) and isinstance(given[i], dict) else {}
            passed = got.get("pass") is True
            reason = str(got.get("reason") or "the judge did not answer this criterion")
            results.append({"criterion": criterion, "pass": passed, "reason": reason})
            if not passed:
                failures.append({"kind": "visual", "test": test_name, "label": cap["label"],
                                 "expected": criterion, "actual": reason, "photo": str(cap["photo"])})
        verdict["captures"].append({"label": cap["label"], "photo": str(cap["photo"]), "criteria": results})
    return failures, verdict

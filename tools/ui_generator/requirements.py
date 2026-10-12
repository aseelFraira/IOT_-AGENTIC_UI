"""Independent acceptance criteria for a request, written before anything is built.

The UI builder also writes its own tests, so on its own it can quietly deliver
less than was asked (fake "AR1" labels for Arabic keys, 9 keys for a keyboard)
and then test exactly that. Here the judge's model (judge.call_settings: its own
key and model) reads only the user's requests and states:

  must_see      facts the first photo must show; added to the initial-render check
  must_do       interactions; given to the builder, which must test them
  not_possible  what this device can't do; the builder must not fake these, and
                the chat tells the user

It never sees the builder's code or conversation.
"""

import json
from pathlib import Path

import gemini_client
import judge

PROMPT_FILE = Path(__file__).with_name("requirements_prompt.txt")


def _clean_list(value, limit):
    return [str(v).strip() for v in value if str(v).strip()][:limit] if isinstance(value, list) else []


def derive(requests, generate=None) -> dict:
    """requests: the user's requests in order (first = created the screen).
    Returns {"must_see": [...], "must_do": [...], "not_possible": [{"item", "why"}]}."""
    generate = generate or gemini_client.generate
    text = "\n".join(f"{n}. {r}" for n, r in enumerate(requests, 1))
    messages = [("user", "The user's requests, in order:\n" + text)]
    system = PROMPT_FILE.read_text(encoding="utf-8")
    last_error = None
    for _ in range(2):
        reply = generate(system, messages, temperature=0.0, json_output=True, **judge.call_settings())
        try:
            data = json.loads(judge._strip_fence(reply))
            if not isinstance(data, dict):
                raise ValueError("not an object")
        except ValueError as e:
            last_error = e
            continue
        impossible = [
            {"item": str(x.get("item", "")).strip(), "why": str(x.get("why", "")).strip()}
            for x in data.get("not_possible", []) if isinstance(x, dict) and x.get("item")
        ][:10]
        return {"must_see": _clean_list(data.get("must_see"), 12),
                "must_do": _clean_list(data.get("must_do"), 12),
                "not_possible": impossible}
    raise gemini_client.GeminiError(f"requirements reply was not valid JSON: {last_error}")


def for_builder(req: dict) -> str:
    """The text added to the builder's request."""
    parts = ["Acceptance criteria from an independent reviewer. A photo of your screen "
             "will be checked against the first list, so meet every item fully:"]
    parts += [f"- {s}" for s in req["must_see"]]
    if req["must_do"]:
        parts.append("Interactions that must work; write tests (expect and capture steps) for each:")
        parts += [f"- {s}" for s in req["must_do"]]
    if req["not_possible"]:
        parts.append("NOT possible on this device. Do not fake these with placeholder text, "
                     "stand-in labels or a reduced version; leave them out:")
        parts += [f"- {x['item']} ({x['why']})" for x in req["not_possible"]]
    return "\n".join(parts)

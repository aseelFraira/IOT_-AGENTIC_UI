"""Command-line version of the generator.

    python tools/ui_generator/generate.py "a light switch with a brightness slider" --flash
    python tools/ui_generator/generate.py "..." --test            # flash, run the tests, use the camera
    python tools/ui_generator/generate.py "..." --code-file reply.txt   # skip Gemini

--code-file takes a saved reply (both files with their marker lines), or a bare
generated_ui.cpp together with --spec-file.
"""

import argparse
import sys
from pathlib import Path

import gemini_client
import pipeline
import spec


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    ap.add_argument("request", help="the interface you want")
    ap.add_argument("--flash", action="store_true", help="upload to the board after it compiles")
    ap.add_argument("--test", action="store_true", help="flash, then run the UI's tests on the board")
    ap.add_argument("--port", help="upload/serial port, e.g. COM4")
    ap.add_argument("--code-file", help="use this file as the model reply instead of calling Gemini")
    ap.add_argument("--spec-file", help="ui_spec.json to pair with a bare --code-file")
    args = ap.parse_args()

    get_code = None
    if args.code_file:
        text = Path(args.code_file).read_text(encoding="utf-8")
        if args.spec_file:
            text = spec.join_reply(text, Path(args.spec_file).read_text(encoding="utf-8"))
        get_code = lambda system_prompt, messages: text
        pipeline.MAX_ATTEMPTS = 1  # a fixed reply can't fix itself, so don't retry it

    try:
        result = pipeline.run(args.request, flash=args.flash or args.test, port=args.port,
                              get_code=get_code, test=args.test)
    except (gemini_client.GeminiError, RuntimeError) as e:
        print(f"Error: {e}")
        sys.exit(2)
    print(f"Result: {'accepted' if result.ok else 'not accepted'}; tests {result.tests}"
          + (f" ({result.checks_passed}/{result.checks_total} checks)" if result.checks_total else "")
          + f"; details in {result.run_dir}")
    sys.exit(0 if result.ok else 1)


if __name__ == "__main__":
    main()

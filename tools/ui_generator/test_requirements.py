import json
import os
import unittest
from unittest import mock

import pipeline
import requirements
import server
import spec
from test_pipeline_theme import CODE, ThemeFlowTests
from test_spec import GOOD_SPEC

REQ = {"must_see": ["The keyboard shows all 26 letters A to Z"],
       "must_do": ["Pressing a letter adds it to the note"],
       "not_possible": [{"item": "Arabic letters on the keys", "why": "the screen's font has no Arabic letters"}]}


class DeriveTests(unittest.TestCase):
    def test_parses_and_uses_the_judge_account(self):
        calls = []

        def generate(system, messages, **kw):
            calls.append((messages, kw))
            return json.dumps(dict(REQ, extra="ignored"))

        with mock.patch.dict(os.environ, {"GEMINI_JUDGE_API_KEY": "paid", "GEMINI_JUDGE_MODEL": "m"}):
            out = requirements.derive(["a keyboard", "make it bigger"], generate=generate)
        self.assertEqual(out, REQ)
        self.assertIn("1. a keyboard\n2. make it bigger", calls[0][0][0][1])
        self.assertEqual(calls[0][1]["key_var"], "GEMINI_JUDGE_API_KEY")
        self.assertTrue(calls[0][1]["json_output"])

    def test_builder_text_forbids_faking(self):
        text = requirements.for_builder(REQ)
        self.assertIn("all 26 letters", text)
        self.assertIn("Do not fake", text)
        self.assertIn("Arabic letters on the keys", text)

    def test_earlier_requests_strip_the_preamble(self):
        history = [("user", "Create this user interface:\na keyboard"), ("model", "code"),
                   ("user", "Change the current interface as follows. ...:\nbigger keys")]
        self.assertEqual(pipeline.earlier_requests(history), ["a keyboard", "bigger keys"])


class PipelineRequirementTests(ThemeFlowTests):
    """Reuses the fake board/compile setup from the theme tests."""

    def test_requirements_reach_the_builder_and_the_first_photo(self):
        seen = {}
        reply = spec.join_reply(CODE, json.dumps(GOOD_SPEC))

        def get_code(system, messages):
            seen["prompt"] = messages[-1][1]
            return reply

        real_run_tests = pipeline.run_tests

        def run_tests(*a, **kw):
            seen["must_see"] = kw.get("must_see")
            return real_run_tests(*a, **kw)

        with mock.patch.object(pipeline, "run_tests", run_tests):
            r = pipeline.run("a keyboard", flash=True, test=True, log=lambda *_: None,
                             get_code=get_code, get_requirements=lambda reqs: REQ)
        self.assertIn("all 26 letters", seen["prompt"])
        self.assertIn("Do not fake", seen["prompt"])
        self.assertEqual(seen["must_see"], REQ["must_see"])
        self.assertEqual(r.not_possible, REQ["not_possible"])
        message = server.result_message(r)
        self.assertIn("What this screen can't do", message)
        self.assertIn("no Arabic letters", message)

    # The inherited theme tests run here too; skip the duplicates.
    test_light_is_switched_on_only_after_dark_checks_pass = None
    test_failed_light_check_puts_dark_back = None
    test_crash_goes_back_to_the_ai_and_is_not_accepted = None
    test_dark_ui_never_touches_light = None


if __name__ == "__main__":
    unittest.main()

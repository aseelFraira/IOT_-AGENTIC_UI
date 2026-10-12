import os
import unittest
from unittest import mock

import gemini_client as g

OK = {"candidates": [{"content": {"parts": [{"text": "code"}]}}]}


def err(retryable=False, switch=False, msg="x"):
    return g.GeminiError("detail", msg, retryable=retryable, switch_model=switch)


class SeparateKeyTests(unittest.TestCase):
    def test_key_var_and_no_fallback(self):
        env = {"GEMINI_API_KEY": "free", "GEMINI_JUDGE_API_KEY": "paid", "GEMINI_FALLBACK_MODEL": "fb"}
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(g, "_request", side_effect=err(switch=True)) as req:
            with self.assertRaises(g.GeminiError):
                g.generate("s", [("user", "x")], model="judge-model", key_var="GEMINI_JUDGE_API_KEY",
                           fallback_model="", sleep=lambda _: None)
        self.assertEqual([(c.args[0], c.args[2]) for c in req.call_args_list], [("judge-model", "paid")])

    def test_missing_judge_key_names_the_variable(self):
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "free"}, clear=True):
            with self.assertRaisesRegex(g.GeminiError, "GEMINI_JUDGE_API_KEY"):
                g.generate("s", [("user", "x")], key_var="GEMINI_JUDGE_API_KEY")

    def test_judge_settings(self):
        import judge
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "free", "GEMINI_JUDGE_API_KEY": "paid",
                                          "GEMINI_JUDGE_MODEL": "m"}, clear=True):
            self.assertEqual(judge.call_settings(),
                             {"key_var": "GEMINI_JUDGE_API_KEY", "model": "m", "fallback_model": ""})
        with mock.patch.dict(os.environ, {"GEMINI_API_KEY": "free"}, clear=True):
            self.assertEqual(judge.call_settings(),
                             {"key_var": "GEMINI_API_KEY", "model": None, "fallback_model": None})


class RetryTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"GEMINI_API_KEY": "k", "GEMINI_MODEL": "primary",
                                               "GEMINI_FALLBACK_MODEL": "fallback"})
        patcher.start()
        self.addCleanup(patcher.stop)
        self.sleeps = []

    def run_generate(self, side_effect):
        with mock.patch.object(g, "_request", side_effect=side_effect) as req:
            out = g.generate("sys", [("user", "hi")], sleep=self.sleeps.append)
        return out, req

    def test_overload_is_retried_then_succeeds(self):
        out, req = self.run_generate([err(retryable=True), err(retryable=True), OK])
        self.assertEqual(out, "code")
        self.assertEqual([c.args[0] for c in req.call_args_list], ["primary"] * 3)
        self.assertEqual(self.sleeps, [4, 10])

    def test_quota_switches_to_fallback_without_waiting(self):
        out, req = self.run_generate([err(switch=True), OK])
        self.assertEqual(out, "code")
        self.assertEqual([c.args[0] for c in req.call_args_list], ["primary", "fallback"])
        self.assertEqual(self.sleeps, [])

    def test_retries_exhausted_then_fallback(self):
        out, req = self.run_generate([err(retryable=True)] * 4 + [OK])
        self.assertEqual(out, "code")
        self.assertEqual([c.args[0] for c in req.call_args_list], ["primary"] * 4 + ["fallback"])
        self.assertEqual(self.sleeps, [4, 10, 20])

    def test_bad_key_fails_immediately(self):
        with self.assertRaises(g.GeminiError):
            self.run_generate([err(msg="bad key")])
        self.assertEqual(self.sleeps, [])

    def test_everything_down_raises_last_error_with_friendly_text(self):
        with self.assertRaises(g.GeminiError) as ctx:
            self.run_generate([err(retryable=True, msg="overloaded")] * 8)
        self.assertEqual(ctx.exception.user_message, "overloaded")

    def test_same_fallback_as_primary_is_not_repeated(self):
        with mock.patch.dict(os.environ, {"GEMINI_FALLBACK_MODEL": "primary"}):
            with self.assertRaises(g.GeminiError):
                self.run_generate([err(switch=True)])

    def test_missing_key_message_is_friendly(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(g.GeminiError) as ctx:
                g.generate("sys", [("user", "hi")])
        self.assertIn("API key is missing", ctx.exception.user_message)


if __name__ == "__main__":
    unittest.main()

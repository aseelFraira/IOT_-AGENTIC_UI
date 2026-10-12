import os
import unittest
from unittest import mock

import gemini_client
import openai_client as o
import pipeline

OK = {"choices": [{"message": {"content": "code"}}]}


class OpenAITests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.dict(os.environ, {"OPENAI_API_KEY": "k", "OPENAI_MODEL": "m"})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_request_shape(self):
        with mock.patch.object(o, "_request", return_value=OK) as req:
            out = o.generate("sys", [("user", "a"), ("model", "b"), ("user", [{"text": "c"}])],
                             temperature=0.2, json_output=False)
        self.assertEqual(out, "code")
        body, key = req.call_args.args[0], req.call_args.args[1]
        self.assertEqual(key, "k")
        self.assertEqual(body["model"], "m")
        self.assertEqual([(m["role"], m["content"]) for m in body["messages"]],
                         [("system", "sys"), ("user", "a"), ("assistant", "b"), ("user", "c")])
        self.assertNotIn("temperature", body)

    def test_retries_then_succeeds(self):
        busy = gemini_client.GeminiError("x", retryable=True)
        sleeps = []
        with mock.patch.object(o, "_request", side_effect=[busy, OK]):
            self.assertEqual(o.generate("s", [("user", "x")], sleep=sleeps.append), "code")
        self.assertEqual(sleeps, [4])

    def test_missing_key_or_model(self):
        with mock.patch.dict(os.environ, {"OPENAI_API_KEY": ""}):
            with self.assertRaisesRegex(gemini_client.GeminiError, "OPENAI_API_KEY"):
                o.generate("s", [("user", "x")])
        with mock.patch.dict(os.environ, {"OPENAI_MODEL": ""}):
            with self.assertRaisesRegex(gemini_client.GeminiError, "OPENAI_MODEL"):
                o.generate("s", [("user", "x")])

    def test_provider_choice(self):
        with mock.patch.dict(os.environ, {"GENERATOR_PROVIDER": "openai"}):
            self.assertIs(pipeline.default_generator(), o.generate)
        with mock.patch.dict(os.environ, {"GENERATOR_PROVIDER": ""}):
            self.assertIs(pipeline.default_generator(), gemini_client.generate)
        with mock.patch.dict(os.environ, {"GENERATOR_PROVIDER": "claude"}):
            with self.assertRaises(RuntimeError):
                pipeline.default_generator()


if __name__ == "__main__":
    unittest.main()

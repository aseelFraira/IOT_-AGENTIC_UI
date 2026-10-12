import unittest
from unittest import mock

import gemini_client
import pipeline
import server

CODE_1 = "// ui v1\nvoid generated_ui_create(void){}\n"
CODE_2 = "// ui v2\nvoid generated_ui_create(void){}\n"


class SessionTests(unittest.TestCase):
    def setUp(self):
        server.sessions.clear()

    def turn(self, sid, request, result):
        """Runs handle_generate with pipeline.run replaced; returns (ok, message, sid, run_mock).
        result is an exception, a pipeline.Result, or a short (ok, reply) tuple."""
        if isinstance(result, tuple):
            ok, reply = result
            result = pipeline.Result(ok=ok, reply=reply, flashed=ok)
        with mock.patch.object(pipeline, "run", side_effect=result if isinstance(result, Exception) else None,
                               return_value=None if isinstance(result, Exception) else result) as run:
            ok, message, sid = server.handle_generate(sid, request)
        return ok, message, sid, run

    def test_build_messages_first_and_follow_up(self):
        msgs, text = pipeline.build_messages("a big button")
        self.assertEqual(msgs, [("user", text)])
        self.assertTrue(text.startswith("Create this user interface:"))

        history = [("user", "first"), ("model", CODE_1)]
        msgs, text = pipeline.build_messages("make it red", history)
        self.assertEqual(msgs[:2], history)
        self.assertEqual(msgs[2], ("user", text))
        self.assertIn("Change the current interface", text)
        self.assertIn("make it red", text)

    def test_follow_up_carries_previous_code(self):
        ok, _, sid, _ = self.turn(None, "a button", (True, CODE_1))
        self.assertTrue(ok)
        ok, _, sid2, run = self.turn(sid, "make it red", (True, CODE_2))
        self.assertEqual(sid, sid2)
        history_sent = run.call_args.kwargs["history"]
        self.assertEqual(history_sent[-1], ("model", CODE_1))
        self.assertEqual(server.sessions[sid]["history"][-1], ("model", CODE_2))

    def test_user_never_sees_code(self):
        ok, message, sid, _ = self.turn(None, "a button", (True, CODE_1))
        chat_text = " ".join(m["text"] for m in server.sessions[sid]["chat"]) + message
        self.assertNotIn("generated_ui_create", chat_text)
        self.assertNotIn("ui v1", chat_text)

    def test_failed_build_keeps_session_unchanged(self):
        _, _, sid, _ = self.turn(None, "a button", (True, CODE_1))
        ok, message, _, _ = self.turn(sid, "something impossible", (False, ""))
        self.assertFalse(ok)
        self.assertIn("couldn't build", message)
        self.assertEqual(server.sessions[sid]["history"][-1], ("model", CODE_1))

    def test_flash_failure_is_reported_and_keeps_new_code(self):
        ok, message, sid, _ = self.turn(None, "a button", (False, CODE_1))
        self.assertFalse(ok)
        self.assertIn("USB", message)
        self.assertEqual(server.sessions[sid]["history"][-1], ("model", CODE_1))

    def test_test_outcomes_are_reported_plainly(self):
        passed = pipeline.Result(ok=True, reply=CODE_1, flashed=True, tests="passed",
                                 checks_passed=9, checks_total=9, photos=4)
        ok, message, sid, run = self.turn(None, "a button", passed)
        self.assertTrue(ok)
        self.assertIn("passed all 9 checks", message)
        self.assertIn("4 photos", message)
        self.assertTrue(run.call_args.kwargs["test"] in (True, False))

        not_run = pipeline.Result(ok=True, reply=CODE_2, flashed=True, tests="not_run",
                                  problem="COM4 busy")
        ok, message, _, _ = self.turn(sid, "make it red", not_run)
        self.assertTrue(ok)
        self.assertIn("couldn't check it: the board didn't answer", message)
        self.assertNotIn("COM4", message)
        self.assertEqual(server.sessions[sid]["history"][-1], ("model", CODE_2))

        cam = pipeline.Result(ok=True, reply=CODE_2, flashed=True, tests="not_run",
                              problem="Camera 1 gives no picture.")
        ok, message, _, _ = self.turn(sid, "again", cam)
        self.assertIn("close it", message)

        failed = pipeline.Result(ok=False, tests="failed", checks_passed=3, checks_total=5)
        ok, message, _, _ = self.turn(sid, "something odd", failed)
        self.assertFalse(ok)
        self.assertIn("previous interface is back", message)
        self.assertEqual(server.sessions[sid]["history"][-1], ("model", CODE_2))

    def test_errors_show_only_friendly_text(self):
        secret_detail = "HTTP 403: key AIza-SECRET rejected"
        err = gemini_client.GeminiError(secret_detail, "The assistant rejected the request. The API key may be wrong.")
        ok, message, sid, _ = self.turn(None, "a button", err)
        self.assertFalse(ok)
        self.assertNotIn("SECRET", message)
        self.assertNotIn("SECRET", str(server.sessions[sid]["chat"]))
        self.assertIn("rejected", message)

        ok, message, _, _ = self.turn(None, "a button", RuntimeError("pio not found at C:\\secret\\path"))
        self.assertNotIn("secret", message)

    def test_history_is_trimmed(self):
        sid = None
        for i in range(server.MAX_HISTORY_TURNS + 3):
            _, _, sid, _ = self.turn(sid, f"change {i}", (True, f"// v{i}\n"))
        history = server.sessions[sid]["history"]
        self.assertEqual(len(history), 2 * server.MAX_HISTORY_TURNS)
        self.assertEqual(history[0][0], "user")
        self.assertEqual(history[-1], ("model", f"// v{server.MAX_HISTORY_TURNS + 2}\n"))

    def test_unknown_or_malformed_session_id_gets_a_new_session(self):
        for bad in (None, "", "../../etc", "zzzz", "0" * 16):
            sid, session = server.get_session(bad)
            self.assertRegex(sid, r"^[0-9a-f]{16}$")
            self.assertEqual(session["history"], [])

    def test_sessions_are_separate(self):
        _, _, a, _ = self.turn(None, "one", (True, CODE_1))
        _, _, b, _ = self.turn(None, "two", (True, CODE_2))
        self.assertNotEqual(a, b)
        self.assertEqual(server.sessions[a]["history"][-1], ("model", CODE_1))


if __name__ == "__main__":
    unittest.main()

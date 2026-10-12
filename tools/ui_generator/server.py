"""Local web UI: describe the interface you want, then keep asking for changes.

    python tools/ui_generator/server.py            # then open http://127.0.0.1:8765

The user only sees plain-language messages. Code, rule violations and compiler
output stay in this terminal (the developer log).

A session remembers the conversation, so follow-ups ("make the button bigger")
change the current interface instead of starting over. Sessions live on the
server, so refreshing the page keeps the conversation.

Listens on 127.0.0.1 only. Requests from other websites are rejected, because
this server can call Gemini with your key and flash the board.
"""

import json
import os
import re
import secrets
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import gemini_client
import pipeline

HOST, PORT = "127.0.0.1", 8765
ALLOWED_HOSTS = {f"127.0.0.1:{PORT}", f"localhost:{PORT}"}
MAX_REQUEST_CHARS = 2000
MAX_HISTORY_TURNS = 4  # earlier (request, code) pairs sent back to Gemini
MAX_CHAT_MESSAGES = 200
FLASH = os.environ.get("FLASH", "1") != "0"  # set FLASH=0 to only compile
TESTS = os.environ.get("TESTS", "1") != "0"  # set TESTS=0 to flash without running the tests
UPLOAD_PORT = os.environ.get("UPLOAD_PORT")

WELCOME = "Hi! Tell me what interface you want on the screen, and I'll build it. You can ask for changes afterwards."

busy = threading.Lock()
sessions = {}  # sid -> {"history": [(role, text)], "chat": [{"who", "text"}]}
sessions_lock = threading.Lock()
SID_RE = re.compile(r"^[0-9a-f]{16}$")

PAGE = """<!doctype html>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Screen designer</title>
<style>
  body { font: 15px system-ui, sans-serif; max-width: 720px; margin: 0 auto; padding: 1rem;
         display: flex; flex-direction: column; height: 100vh; box-sizing: border-box; }
  header { display: flex; justify-content: space-between; align-items: center; }
  h1 { font-size: 1.2rem; margin: .2rem 0; }
  #chat { flex: 1; overflow-y: auto; border: 1px solid #ddd; border-radius: 8px; padding: .75rem; margin: .5rem 0; }
  .msg { margin: .4rem 0; padding: .55rem .8rem; border-radius: 10px; max-width: 85%; white-space: pre-wrap; }
  .user { background: #dbeafe; margin-left: auto; }
  .bot { background: #f1f1f1; }
  .bot.err { background: #fde2e2; }
  form { display: flex; gap: .5rem; }
  textarea { flex: 1; font: inherit; padding: .5rem; height: 3.2rem; resize: none; }
  button { font: inherit; padding: .4rem 1rem; }
</style>
<header><h1>Screen designer</h1><button id="reset" type="button">Start over</button></header>
<div id="chat"></div>
<form id="form">
  <textarea id="req" placeholder="Describe the screen, or ask for a change..." maxlength="2000"></textarea>
  <button id="send">Send</button>
</form>
<script>
const $ = id => document.getElementById(id);
let sid = null;
try { sid = localStorage.getItem("sid"); } catch (e) {}

function add(who, text, err) {
  const d = document.createElement("div");
  d.className = "msg " + (who === "user" ? "user" : "bot") + (err ? " err" : "");
  d.textContent = text;
  $("chat").appendChild(d);
  $("chat").scrollTop = $("chat").scrollHeight;
  return d;
}
async function api(path, body) {
  const r = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" },
                                body: JSON.stringify(body) });
  return r.json();
}
async function load(reset) {
  const j = await api(reset ? "/api/reset" : "/api/session", { sid });
  sid = j.sid;
  try { localStorage.setItem("sid", sid); } catch (e) {}
  $("chat").textContent = "";
  j.chat.forEach(m => add(m.who, m.text, m.err));
}
$("reset").onclick = () => load(true);
$("req").addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("form").requestSubmit(); }
});
$("form").onsubmit = async e => {
  e.preventDefault();
  const text = $("req").value.trim();
  if (!text) return;
  $("req").value = "";
  add("user", text);
  $("send").disabled = true;
  const wait = add("bot", "Working on it... building and testing on the screen takes a few minutes.");
  try {
    const j = await api("/api/generate", { sid, request: text });
    wait.textContent = j.message;
    if (!j.ok) wait.classList.add("err");
    if (j.sid && j.sid !== sid) {
      sid = j.sid;
      try { localStorage.setItem("sid", sid); } catch (e) {}
    }
  } catch (err) {
    wait.textContent = "Something went wrong talking to the tool. Please try again.";
    wait.classList.add("err");
  }
  $("send").disabled = false;
};
load(false);
</script>
"""


def friendly_failure(exc):
    """User-facing text for an exception. The technical detail is only logged."""
    if isinstance(exc, gemini_client.GeminiError):
        return exc.user_message
    return "The build tools aren't available on this computer."


def get_session(sid):
    with sessions_lock:
        if not sid or not SID_RE.match(sid) or sid not in sessions:
            sid = secrets.token_hex(8)
            sessions[sid] = {"history": [], "chat": [{"who": "bot", "text": WELCOME}]}
        return sid, sessions[sid]


def add_chat(session, who, text, err=False):
    session["chat"].append({"who": who, "text": text, "err": err})
    del session["chat"][:-MAX_CHAT_MESSAGES]


def developer_log(line):
    sys.stderr.write("[generator] " + line + "\n")


def result_message(result):
    """Plain-language summary of a pipeline.Result for the user."""
    message = _result_message(result)
    if result.not_possible:
        message += "\n\nWhat this screen can't do:\n" + "\n".join(
            f"- {x['item']}: {x['why']}" for x in result.not_possible)
    return message


def _result_message(result):
    more = " Tell me if you want anything changed."
    if result.ok and result.theme_wanted == "light":
        if result.theme == "light" and result.tests == "passed":
            more = (" I checked it in dark colours, which the camera sees best, then switched it to "
                    "the light colours you asked for." + more)
        elif result.theme != "light" and result.tests == "passed":
            more = (" It's showing dark colours for now: the light version didn't pass its final "
                    "check, so I kept the version that did." + more)
        elif result.theme != "light" and result.tests == "not_run":
            more = " It stays in dark colours until the checks can run." + more
    if result.ok and result.tests == "passed":
        photos = f", including {result.photos} photos of the screen" if result.photos else ""
        return f"Done! Your interface is on the screen and passed all {result.checks_total} checks{photos}." + more
    if result.ok and result.tests == "not_run":
        if "Camera" in result.problem or "camera" in result.problem:
            why = ("the camera couldn't see the screen properly. If the camera preview or another "
                   "app (Zoom, Teams) is open, close it and ask again")
        else:
            why = "the board didn't answer. Check the USB cable and ask again"
        return ("I sent your interface to the board, but couldn't check it: " + why + "." + more)
    if result.ok and result.flashed:
        return "Done! Your interface is on the screen." + more
    if result.ok:
        return "Done! Your interface is built (not sent to the board)." + more
    if result.reply:
        return "The interface is ready, but I couldn't send it to the board. Check the USB cable and try again."
    if result.tests == "failed":
        return ("I couldn't build a version that passed its checks on the screen, so your previous "
                "interface is back. Try describing it a different way, or ask for something simpler.")
    return "I couldn't build that version. Try describing it a different way, or ask for something simpler."


def handle_generate(sid, request):
    """Runs one turn. Returns the plain-language reply for the user."""
    sid, session = get_session(sid)
    add_chat(session, "user", request)
    _, user_text = pipeline.build_messages(request, session["history"])

    try:
        result = pipeline.run(
            request, flash=FLASH, port=UPLOAD_PORT, log=developer_log,
            history=list(session["history"]), test=TESTS
        )
    except (gemini_client.GeminiError, RuntimeError) as e:
        developer_log(f"error: {e}")
        message, ok = friendly_failure(e), False
    else:
        ok = result.ok
        if result.reply:  # accepted, so it is now the current interface even if flashing failed
            session["history"] += [("user", user_text), ("model", result.reply)]
            del session["history"][: -2 * MAX_HISTORY_TURNS]
        message = result_message(result)

    add_chat(session, "bot", message, err=not ok)
    return ok, message, sid


class Handler(BaseHTTPRequestHandler):
    def _send(self, status, body, content_type="application/json"):
        data = body if isinstance(body, bytes) else body.encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", content_type + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _trusted(self) -> bool:
        if self.headers.get("Host") not in ALLOWED_HOSTS:
            return False
        origin = self.headers.get("Origin")
        return origin is None or origin in {f"http://{h}" for h in ALLOWED_HOSTS}

    def do_GET(self):
        if self.path == "/" and self._trusted():
            self._send(200, PAGE, "text/html")
        else:
            self._send(404, json.dumps({"error": "not found"}))

    def do_POST(self):
        if self.path not in ("/api/session", "/api/reset", "/api/generate") or not self._trusted():
            return self._send(403, json.dumps({"error": "forbidden"}))
        if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
            return self._send(415, json.dumps({"error": "JSON only"}))
        try:
            length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(min(length, 10_000)))
            if not isinstance(payload, dict):
                raise ValueError
            sid = payload.get("sid")
            sid = sid if isinstance(sid, str) else None
        except (ValueError, TypeError):
            return self._send(400, json.dumps({"error": "bad request"}))

        if self.path == "/api/reset":
            with sessions_lock:
                sessions.pop(sid, None)
            sid = None
        if self.path in ("/api/session", "/api/reset"):
            sid, session = get_session(sid)
            return self._send(200, json.dumps({"sid": sid, "chat": session["chat"]}))

        request = payload.get("request")
        if not isinstance(request, str) or not request.strip() or len(request) > MAX_REQUEST_CHARS:
            return self._send(400, json.dumps({"error": f"request must be 1-{MAX_REQUEST_CHARS} characters"}))
        if not busy.acquire(blocking=False):
            return self._send(409, json.dumps({
                "ok": False, "message": "I'm still working on the previous request. Please wait a moment."}))
        try:
            ok, message, sid = handle_generate(sid, request.strip())
            self._send(200, json.dumps({"ok": ok, "message": message, "sid": sid}))
        finally:
            busy.release()

    def log_message(self, fmt, *args):
        sys.stderr.write("[server] " + fmt % args + "\n")


if __name__ == "__main__":
    if not os.environ.get("GEMINI_API_KEY"):
        print("Warning: GEMINI_API_KEY is not set; generation will fail until it is.")
    tests = "off" if not (FLASH and TESTS) else (
        f"on, camera {os.environ['CAMERA_INDEX']}" if os.environ.get("CAMERA_INDEX") else "on, no camera")
    print(f"Screen designer at http://{HOST}:{PORT}  (flash to board: {'on' if FLASH else 'off'}; "
          f"tests: {tests}; Ctrl+C to stop)")
    ThreadingHTTPServer((HOST, PORT), Handler).serve_forever()

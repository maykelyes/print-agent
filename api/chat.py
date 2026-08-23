from http.server import BaseHTTPRequestHandler
import anthropic, os, json, signal

AGENT_ID = "agent_01GuTCe6YuYJjBm3QcebjoHR"
ENV_ID = "env_01DtrvHsgenRLkMLJamvcxJm"
MEMORY_ID = "memstore_01MR2FvnrmX5rBbCtj3KdKxk"
STREAM_TIMEOUT = 55  # seconds – stay under Vercel's 60s maxDuration

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    timeout=55.0,
)

class _Timeout(Exception):
    pass

def _alarm(signum, frame):
    raise _Timeout()

class handler(BaseHTTPRequestHandler):
    def _json_response(self, code, data):
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(data, ensure_ascii=False).encode())

    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            msg = body["message"]
            session_id = body.get("session_id")

            # create session if needed
            if not session_id:
                s = client.beta.sessions.create(
                    agent=AGENT_ID,
                    environment_id=ENV_ID,
                    resources=[{
                        "type": "memory_store",
                        "memory_store_id": MEMORY_ID,
                        "access": "read_write",
                        "instructions": "בכל פעם שאתה לומד עובדה, מחיר או כלל חדש, עדכן את הקובץ המתאים.",
                    }],
                )
                session_id = s.id

            client.beta.sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": msg}]}],
            )

            reply = ""
            # Set an alarm to avoid hanging forever (Unix only, safe on Vercel)
            try:
                signal.signal(signal.SIGALRM, _alarm)
                signal.alarm(STREAM_TIMEOUT)
            except (AttributeError, OSError):
                pass  # Windows / environments without SIGALRM – rely on SDK timeout

            try:
                for event in client.beta.sessions.events.stream(session_id=session_id):
                    if event.type == "agent.message":
                        for block in event.content:
                            if hasattr(block, "text"):
                                reply += block.text
                    if event.type in ("session.status_idle", "session.status_error"):
                        break
            except _Timeout:
                if not reply:
                    reply = "⏳ הסוכן לא הספיק לענות בזמן. נסה לשלוח את ההודעה שוב."
            finally:
                try:
                    signal.alarm(0)
                except (AttributeError, OSError):
                    pass

            if not reply:
                reply = "לא התקבלה תשובה מהסוכן. נסה שוב."

            self._json_response(200, {"reply": reply, "session_id": session_id})

        except Exception as e:
            self._json_response(500, {
                "reply": f"שגיאה: {str(e)}. נסה שוב או פתח שיחה חדשה.",
                "session_id": body.get("session_id") if "body" in dir() else None,
            })

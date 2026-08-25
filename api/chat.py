from http.server import BaseHTTPRequestHandler
import anthropic, os, json

AGENT_ID = "agent_01GuTCe6YuYJjBm3QcebjoHR"
ENV_ID = "env_01DtrvHsgenRLkMLJamvcxJm"
MEMORY_ID = "memstore_01MR2FvnrmX5rBbCtj3KdKxk"

client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY"),
    timeout=120.0,
)

class handler(BaseHTTPRequestHandler):
    def do_POST(self):
        try:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        except Exception:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"error":"Bad request"}')
            return

        msg = body["message"]
        session_id = body.get("session_id")

        # ---- SSE stream ----
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def sse(data):
            self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        try:
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

            sse({"type": "session", "session_id": session_id})

            client.beta.sessions.events.send(
                session_id=session_id,
                events=[{"type": "user.message", "content": [{"type": "text", "text": msg}]}],
            )

            has_text = False
            for event in client.beta.sessions.events.stream(session_id=session_id):
                if event.type == "agent.message":
                    for block in event.content:
                        if hasattr(block, "text"):
                            sse({"type": "text", "text": block.text})
                            has_text = True
                elif event.type in ("session.status_idle", "session.status_error"):
                    break
                else:
                    # Forward progress events so the connection stays alive
                    sse({"type": "status", "event": event.type})

            if not has_text:
                sse({"type": "text", "text": "לא התקבלה תשובה מהסוכן. נסה שוב."})

        except Exception as e:
            sse({"type": "error", "text": f"שגיאה: {str(e)}. נסה שוב או פתח שיחה חדשה."})

        sse({"type": "done"})

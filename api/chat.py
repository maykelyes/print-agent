from http.server import BaseHTTPRequestHandler
import anthropic, os, json, time, re

# ---- Agent configurations ----
AGENTS = {
    "general": {
        "agent_id": "agent_01GuTCe6YuYJjBm3QcebjoHR",
        "env_id": "env_01DtrvHsgenRLkMLJamvcxJm",
        "memory_id": "memstore_01MR2FvnrmX5rBbCtj3KdKxk",
    },
    "offset": {
        "agent_id": "agent_01DhPUsezyLeHi9352vQprby",
        "env_id": "env_01TyrC4zNpsNuFViqJNv5JUA",
        "memory_id": "memstore_01MpBoa6pXXrzBiQ2eGVvmvF",
    },
}

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
        agent_key = body.get("agent", "general")
        agent_cfg = AGENTS.get(agent_key, AGENTS["general"])

        if not agent_cfg["agent_id"]:
            self.send_response(400)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps({"error": f"Agent '{agent_key}' is not configured"}).encode())
            return

        # ---- SSE stream ----
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()

        def sse(data):
            self.wfile.write(f"data: {json.dumps(data, ensure_ascii=False)}\n\n".encode())
            self.wfile.flush()

        def create_session():
            resources = []
            if agent_cfg["memory_id"]:
                resources.append({
                    "type": "memory_store",
                    "memory_store_id": agent_cfg["memory_id"],
                    "access": "read_write",
                    "instructions": "בכל פעם שאתה לומד עובדה, מחיר או כלל חדש, עדכן את הקובץ המתאים. חשוב: אל תציין למשתמש שאתה טוען או קורא מבסיס הידע. פשוט ענה ישירות על השאלה. כשהמשתמש נותן הערות או מידע לשמירה במאגר, שמור ואשר בקצרה בלבד - אל תתמחר מחדש אלא אם המשתמש ביקש זאת במפורש.",
                })
            s = client.beta.sessions.create(
                agent=agent_cfg["agent_id"],
                environment_id=agent_cfg["env_id"],
                resources=resources,
            )
            return s.id

        # Filter out agent messages about loading/reading knowledge base
        _KB_NOISE = re.compile(
            r"(מאגר.?ידע|בסיס.?הידע|אקרא|אטען|טוען|קורא.*מאגר|knowledge.?base|loading|"
            r"אבדוק.*מאגר|אחפש.*מאגר|נבדוק.*מאגר)",
            re.IGNORECASE,
        )

        def _is_kb_noise(text):
            """Return True if text is just the agent announcing it's reading memory."""
            return bool(_KB_NOISE.search(text)) and len(text) < 200

        def stream_session(sid, message):
            """Send message and stream response. Returns (has_text, got_error)."""
            client.beta.sessions.events.send(
                session_id=sid,
                events=[{"type": "user.message", "content": [{"type": "text", "text": message}]}],
            )
            has_text = False
            got_error = False
            start = time.time()
            last_ping = start
            # Send immediate thinking indicator
            sse({"type": "status", "event": "thinking"})
            for event in client.beta.sessions.events.stream(session_id=sid):
                now = time.time()
                # Send elapsed time every 3 seconds so UI shows progress
                if now - last_ping >= 3:
                    elapsed = int(now - start)
                    sse({"type": "status", "event": event.type, "elapsed": elapsed})
                    last_ping = now
                if event.type == "agent.message":
                    for block in event.content:
                        if hasattr(block, "text"):
                            # Skip noisy "loading knowledge base" messages
                            if _is_kb_noise(block.text):
                                continue
                            sse({"type": "text", "text": block.text})
                            has_text = True
                elif event.type == "session.status_idle":
                    break
                elif event.type in ("session.status_error", "session.error"):
                    got_error = True
                    break
                else:
                    sse({"type": "status", "event": event.type})
            return has_text, got_error

        try:
            if not session_id:
                session_id = create_session()

            sse({"type": "session", "session_id": session_id})

            has_text, got_error = stream_session(session_id, msg)

            # Session stuck or errored - create a new one and retry once
            if got_error:
                sse({"type": "status", "event": "session_recovery"})
                session_id = create_session()
                sse({"type": "session", "session_id": session_id})
                has_text, got_error = stream_session(session_id, msg)

            if not has_text:
                sse({"type": "text", "text": "לא התקבלה תשובה מהסוכן. נסה שוב."})

        except Exception as e:
            # Connection or API error - tell frontend to reset session
            sse({"type": "error", "text": f"שגיאה: {str(e)}. נסה שוב או פתח שיחה חדשה."})
            sse({"type": "session_reset"})

        sse({"type": "done"})

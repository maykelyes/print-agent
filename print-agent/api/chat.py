from http.server import BaseHTTPRequestHandler
import anthropic, os, json
AGENT_ID = "agent_01GuTCe6YuYJjBm3QcebjoHR"
ENV_ID = "env_01DtrvHsgenRLkMLJamvcxJm"
MEMORY_ID = "memstore_01MR2FvnrmX5rBbCtj3KdKxk"
client = anthropic.Anthropic(api_key=os.environ.get("ANTHROPIC_API_KEY"))
class handler(BaseHTTPRequestHandler):
    def do_POST(self):
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
        for event in client.beta.sessions.events.stream(session_id=session_id):
            if event.type == "agent.message":
                for block in event.content:
                    if hasattr(block, "text"):
                        reply += block.text
            if event.type == "session.status_idle":
                break
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps({
            "reply": reply,
            "session_id": session_id
        }, ensure_ascii=False).encode())

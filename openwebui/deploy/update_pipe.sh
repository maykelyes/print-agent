#!/usr/bin/env bash
# Pull the latest code and load print_agents_pipe.py into Open WebUI (no restart needed:
# Open WebUI reloads a function when its stored content changes).
# Usage (as root on the server): bash /opt/print-agent/openwebui/deploy/update_pipe.sh
set -euo pipefail
cd "$(dirname "$0")"
git -C /opt/print-agent pull -q

docker compose cp ../print_agents_pipe.py open-webui:/tmp/print_agents_pipe.py
docker compose exec -T open-webui python - <<'EOF'
import sqlite3, time
content = open("/tmp/print_agents_pipe.py", encoding="utf-8").read()
db = sqlite3.connect("/app/backend/data/webui.db")
cur = db.execute("update function set content = ?, updated_at = ? where id = 'print_agents'", (content, int(time.time())))
db.commit()
print("pipe updated" if cur.rowcount == 1 else "ERROR: function 'print_agents' not found")
EOF
docker compose exec -T open-webui rm -f /tmp/print_agents_pipe.py

# Open WebUI + what the print agents pipe needs, baked in so restarts don't reinstall anything.
FROM ghcr.io/open-webui/open-webui:v0.12.3

# Open WebUI pins anthropic 0.86, which has no Managed Agents sessions API (tested with 1.15.0)
RUN pip install --no-cache-dir "anthropic==1.15.0"

# UI tweaks (hide "+" menu and voice-mode button). Open WebUI copies build/static on start.
COPY custom.css /app/build/static/custom.css

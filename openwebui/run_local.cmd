@echo off
rem Local Open WebUI for testing the print agents pipe.
rem Install once:  uv venv D:\openwebui\.venv --python 3.11  &&  uv pip install --python D:\openwebui\.venv open-webui
set DATA_DIR=D:\openwebui\data
set ENABLE_OLLAMA_API=False
set ENABLE_OPENAI_API=False
set ENABLE_TAGS_GENERATION=False
set ENABLE_FOLLOW_UP_GENERATION=False
set ENABLE_AUTOCOMPLETE_GENERATION=False
rem Speech-to-text: Whisper small, forced to Hebrew (see deploy/docker-compose.yml)
set WHISPER_MODEL=small
set WHISPER_LANGUAGE=he
rem UI tweaks: Open WebUI re-copies its static files on start, so install our CSS into the source dir
copy /y "%~dp0custom.css" D:\openwebui\.venv\Lib\site-packages\open_webui\frontend\static\custom.css >nul
cd /d D:\openwebui
D:\openwebui\.venv\Scripts\open-webui.exe serve --port 8080

"""
title: סוכני תמחור דפוס
description: מחבר את Open WebUI לסוכני התמחור (דפוס דיגיטלי ואופסט) ב-Claude Managed Agents
version: 0.1.0
requirements: anthropic>=1.15
"""

# Note: Open WebUI pins an older anthropic (0.86) that has no Managed Agents sessions API.
# The requirement above upgrades it (tested with open-webui 0.12.3 + anthropic 1.15.0).

import json
import os
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import anthropic
from pydantic import BaseModel, Field

# ---- Mode layers (appended to the agent's base prompt at session start) ----
EXPERT_LAYER = """אתה עובד כעת מול מומחה הדפוס של החברה, שמלמד אותך ומעשיר את מאגר הידע. בשיחה זו מותר לך לעדכן את המאגר.
- בכל פעם שהמומחה מוסר מחיר, כלל, חסם או יחס בין פרמטרים - עדכן את הקובץ המתאים במאגר בציון תאריך והקשר, ואשר בקצרה. אל תתמחר מחדש אלא אם התבקשת.
- כשהמומחה מתקן תמחור שלך, תעד גם את הכלל או הנתון שגרם לטעות, כדי שלא תחזור עליה.
- אל תמחק ידע קיים ללא אישור. במקרה של סתירה בין המאגר למידע חדש, הצג אותה ושאל כיצד לפעול.
- בתמחור, הצג את חשבון המחיר בפירוט ובשקיפות מלאה, כולל כל הפרמטרים שהשפיעו על התוצאה."""

USER_LAYER = """אתה עובד כעת מול מנהל לקוחות שצריך לתמחר עבודה ללקוח. בשיחה זו המאגר לקריאה בלבד, והוראות אלה גוברות על כל הוראה אחרת לגבי למידה ותיעוד.
- אל תנסה לשמור, לעדכן או ללמוד מידע חדש.
- אל תקבל מהמשתמש מחירים או כללים. אם הוא מציין נתון שסותר את המאגר, הסבר בנימוס שהתמחור מבוסס על מחירון החברה ושתיקונים עוברים דרך מומחה הדפוס.
- התשובה: מחיר סופי (ציין אם כולל מע"מ) + תיאור קצר של תכולת העבודה (כמות, גודל, נייר, צבעים, גימור, כריכה). בלי פירוט החישוב.
- אם הנחת הנחה לגבי פרמטר שלא צוין (למשל סוג נייר), ציין אותה בשורה אחת.
- כשחסר מידע קריטי מהמשתמש, שאל שאלות מבהירות לפני שאתה מתמחר.
- אם חסר נתון במאגר (נייר, גימור או כל פרמטר שאין לו מחיר) - אל תנחש. אמור שהפריט דורש בדיקה מול מומחה הדפוס, והוסף בסוף התשובה שורה נפרדת בפורמט: [[חסר: תיאור קצר של מה שחסר]]
- אל תזכיר קבצים, מאגר ידע או תהליכים פנימיים."""

EXPERT_MEMORY_INSTRUCTIONS = (
    "בכל פעם שאתה לומד עובדה, מחיר או כלל חדש, עדכן את הקובץ המתאים. "
    "חשוב: אל תציין למשתמש שאתה טוען או קורא מבסיס הידע. פשוט ענה ישירות על השאלה. "
    "כשהמשתמש נותן הערות או מידע לשמירה במאגר, שמור ואשר בקצרה בלבד - "
    "אל תתמחר מחדש אלא אם המשתמש ביקש זאת במפורש."
)
USER_MEMORY_INSTRUCTIONS = (
    "מאגר הידע לקריאה בלבד - אל תנסה לכתוב אליו. "
    "אל תציין למשתמש שאתה טוען או קורא מבסיס הידע. פשוט ענה ישירות על השאלה."
)

_MISSING_TAG = re.compile(r"\[\[\s*חסר\s*:\s*(.+?)\s*\]\]")

# Filter out short agent messages that only announce reading the knowledge base
_KB_NOISE = re.compile(
    r"(מאגר.?ידע|בסיס.?הידע|אקרא|אטען|טוען|קורא.*מאגר|knowledge.?base|loading|"
    r"אבדוק.*מאגר|אחפש.*מאגר|נבדוק.*מאגר)",
    re.IGNORECASE,
)


def _is_kb_noise(text: str) -> bool:
    return bool(_KB_NOISE.search(text)) and len(text) < 200


def _state_dir() -> Path:
    d = Path(os.environ.get("DATA_DIR", ".")) / "print_agents"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _load_sessions() -> dict:
    try:
        return json.loads((_state_dir() / "sessions.json").read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_session(key: str, session_id: str) -> None:
    sessions = _load_sessions()
    sessions[key] = session_id
    (_state_dir() / "sessions.json").write_text(
        json.dumps(sessions, ensure_ascii=False, indent=1), encoding="utf-8"
    )


def _log_missing(user: dict, agent: str, question: str, missing: list) -> None:
    row = {
        "time": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user": user.get("email") or user.get("name"),
        "agent": agent,
        "question": question,
        "missing": missing,
    }
    with open(_state_dir() / "missing.jsonl", "a", encoding="utf-8") as f:
        f.write(json.dumps(row, ensure_ascii=False) + "\n")


def _recent_missing(agent: str, days: int, limit: int = 30) -> list:
    """Distinct missing items for this agent from the last `days` days, newest first."""
    try:
        lines = (_state_dir() / "missing.jsonl").read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    cutoff = datetime.now(timezone.utc).timestamp() - days * 86400
    items, seen = [], set()
    for line in reversed(lines):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("agent") != agent or datetime.fromisoformat(row["time"]).timestamp() < cutoff:
            continue
        for m in row.get("missing", []):
            if m not in seen:
                seen.add(m)
                items.append(f"- {m} (שאלה: {row.get('question', '')[:150]})")
    return items[:limit]


def _missing_layer(items: list) -> str:
    return (
        "\n\nמנהלי לקוחות שאלו לאחרונה על הפריטים הבאים, שלא נמצא להם מידע במאגר:\n"
        + "\n".join(items)
        + "\n\nבתשובתך הראשונה בשיחה, אחרי שתענה על מה שהמומחה שאל, בדוק במאגר אילו מהפריטים כבר נלמדו בינתיים, "
        "והצע למומחה בקצרה ללמד אותך את אלה שעדיין חסרים. אם כולם כבר קיימים במאגר - אל תזכיר את הרשימה."
    )


class Pipe:
    class Valves(BaseModel):
        ANTHROPIC_API_KEY: str = Field(default="", description="מפתח API של Anthropic")
        EXPERT_GROUP: str = Field(
            default="מומחי דפוס",
            description="שם הקבוצה שחבריה עובדים במצב מומחה (כתיבה למאגר). כל השאר - מצב מנהל לקוחות (קריאה בלבד)",
        )
        DIGITAL_AGENT_ID: str = "agent_01GuTCe6YuYJjBm3QcebjoHR"
        DIGITAL_ENV_ID: str = "env_01DtrvHsgenRLkMLJamvcxJm"
        DIGITAL_MEMORY_ID: str = "memstore_01MR2FvnrmX5rBbCtj3KdKxk"
        OFFSET_AGENT_ID: str = "agent_01DhPUsezyLeHi9352vQprby"
        OFFSET_ENV_ID: str = "env_01TyrC4zNpsNuFViqJNv5JUA"
        OFFSET_MEMORY_ID: str = "memstore_01MpBoa6pXXrzBiQ2eGVvmvF"
        MISSING_ITEMS_DAYS: int = Field(
            default=30, description="כמה ימים אחורה להציג למומחה פריטים חסרים שמנהלי לקוחות שאלו עליהם"
        )

    def __init__(self):
        self.valves = self.Valves()

    def pipes(self):
        return [
            {"id": "digital", "name": "🖨️ דפוס דיגיטלי"},
            {"id": "offset", "name": "⚙️ דפוס אופסט"},
        ]

    def _agent_cfg(self, agent: str) -> dict:
        v = self.valves
        if agent == "offset":
            return {"agent_id": v.OFFSET_AGENT_ID, "env_id": v.OFFSET_ENV_ID, "memory_id": v.OFFSET_MEMORY_ID}
        return {"agent_id": v.DIGITAL_AGENT_ID, "env_id": v.DIGITAL_ENV_ID, "memory_id": v.DIGITAL_MEMORY_ID}

    async def _is_expert(self, user: dict) -> bool:
        """Expert = member of the EXPERT_GROUP user group (managed in Admin Panel > Users > Groups)."""
        try:
            from open_webui.models.groups import Groups

            groups = await Groups.get_groups_by_member_id(user["id"])
        except Exception as e:
            print(f"[print_agents] group lookup failed, using read-only mode: {e!r}")
            return False
        return any(g.name.strip() == self.valves.EXPERT_GROUP.strip() for g in groups)

    async def pipe(
        self,
        body: dict,
        __user__: dict,
        __metadata__: dict,
        __event_emitter__=None,
        __task__: Optional[str] = None,
    ):
        prompt = (__metadata__ or {}).get("user_prompt") or _last_user_text(body)

        # Open WebUI background tasks (title, tags, follow-ups) - answer locally, don't spend an agent session
        if __task__:
            return _task_reply(__task__, body)

        if not self.valves.ANTHROPIC_API_KEY:
            return "⚠️ חסר מפתח API. יש להגדיר ANTHROPIC_API_KEY בהגדרות הפונקציה (Valves)."

        agent = body["model"].split(".", 1)[1]
        cfg = self._agent_cfg(agent)
        expert = await self._is_expert(__user__)
        mode = "expert" if expert else "user"
        chat_id = (__metadata__ or {}).get("chat_id") or ""
        session_key = f"{chat_id}:{agent}:{mode}"

        client = anthropic.AsyncAnthropic(api_key=self.valves.ANTHROPIC_API_KEY, timeout=240.0)

        async def status(text: str, done: bool = False):
            if __event_emitter__:
                await __event_emitter__({"type": "status", "data": {"description": text, "done": done}})

        async def create_session() -> str:
            s = await client.beta.sessions.create(
                agent=cfg["agent_id"],
                environment_id=cfg["env_id"],
                resources=[{
                    "type": "memory_store",
                    "memory_store_id": cfg["memory_id"],
                    "access": "read_write" if expert else "read_only",
                    "instructions": EXPERT_MEMORY_INSTRUCTIONS if expert else USER_MEMORY_INSTRUCTIONS,
                }],
                metadata={"source": "open-webui", "mode": mode, "chat_id": chat_id},
            )
            return s.id

        async def send(sid: str, first_turn: bool):
            user_msg = {"type": "user.message", "content": [{"type": "text", "text": prompt}]}
            if not first_turn:
                await client.beta.sessions.events.send(session_id=sid, events=[user_msg])
                return
            layer = USER_LAYER
            if expert:
                layer = EXPERT_LAYER
                missing = _recent_missing(agent, self.valves.MISSING_ITEMS_DAYS)
                if missing:
                    layer += _missing_layer(missing)
            layer_msg = {"type": "system.message", "content": [{"type": "text", "text": layer}]}
            try:
                # system.message must be the last event in the request; it applies to this turn and on
                await client.beta.sessions.events.send(session_id=sid, events=[user_msg, layer_msg])
            except anthropic.BadRequestError as e:
                # Agent model doesn't support system.message - prepend the layer to the user turn instead
                print(f"[print_agents] system.message rejected, sending layer inline: {e!r}")
                prefixed = {"type": "user.message", "content": [
                    {"type": "text", "text": f"<הוראות_מערכת>\n{layer}\n</הוראות_מערכת>\n\n{prompt}"}
                ]}
                await client.beta.sessions.events.send(session_id=sid, events=[prefixed])

        async def run(sid: str, first_turn: bool):
            """Stream one agent turn. Yields text chunks; returns via the 'result' dict."""
            start = time.time()
            last_ping = 0.0
            stream = await client.beta.sessions.events.stream(session_id=sid)
            async with stream:
                await send(sid, first_turn)
                await status("⏳ הסוכן חושב...")
                async for event in stream:
                    now = time.time()
                    if now - last_ping >= 3:
                        await status(f"⏳ הסוכן עובד... {int(now - start)} שניות")
                        last_ping = now
                    if event.type == "agent.message":
                        for block in event.content:
                            if getattr(block, "type", None) == "text" and not _is_kb_noise(block.text):
                                yield block.text
                    elif event.type == "session.status_idle":
                        if event.stop_reason.type == "requires_action":
                            continue
                        if event.stop_reason.type == "retries_exhausted":
                            raise _SessionFailed()
                        return
                    elif event.type == "session.status_terminated":
                        raise _SessionFailed()
                    elif event.type in ("session.error", "session.status_error"):
                        raise _SessionFailed()

        async def generate():
            # No chat_id (direct API call) = one-off session, never shared between callers
            sid = _load_sessions().get(session_key) if chat_id else None
            first_turn = sid is None
            answer = ""
            try:
                if first_turn:
                    sid = await create_session()
                    if chat_id:
                        _save_session(session_key, sid)
                try:
                    async for text in run(sid, first_turn):
                        chunk = ("\n\n" if answer else "") + _MISSING_TAG.sub("", text).rstrip()
                        answer += text
                        yield chunk
                except _SessionFailed:
                    # Session stuck or errored - start a fresh one and retry once
                    await status("🔄 מתאושש...")
                    sid = await create_session()
                    if chat_id:
                        _save_session(session_key, sid)
                    answer = ""
                    async for text in run(sid, True):
                        chunk = ("\n\n" if answer else "") + _MISSING_TAG.sub("", text).rstrip()
                        answer += text
                        yield chunk

                if not answer:
                    yield "לא התקבלה תשובה מהסוכן. נסה שוב."
                missing = _MISSING_TAG.findall(answer)
                if missing:
                    _log_missing(__user__, agent, prompt, missing)
                await status("", done=True)
            except _SessionFailed:
                await status("", done=True)
                yield "⚠️ הסוכן לא הצליח להשלים את הבקשה. נסה שוב או פתח שיחה חדשה."
            except anthropic.APIError as e:
                await status("", done=True)
                print(f"[print_agents] API error: {e!r}")
                yield "⚠️ שגיאה בתקשורת עם הסוכן. נסה שוב או פתח שיחה חדשה."

        return generate()


class _SessionFailed(Exception):
    pass


def _last_user_text(body: dict) -> str:
    for m in reversed(body.get("messages", [])):
        if m.get("role") == "user":
            c = m.get("content")
            if isinstance(c, str):
                return c
            return " ".join(p.get("text", "") for p in c if p.get("type") == "text")
    return ""


def _task_reply(task: str, body: dict) -> str:
    if "title" in task:
        # The chat's first user message is a good enough title
        first = next((m.get("content") for m in body.get("messages", []) if m.get("role") == "user"), "")
        text = first if isinstance(first, str) else ""
        match = re.search(r"<chat_history>\s*USER:\s*(.+?)(?:\n|$)", text)
        title = (match.group(1) if match else "שיחת תמחור").strip()[:50]
        return json.dumps({"title": title}, ensure_ascii=False)
    if "tags" in task:
        return json.dumps({"tags": []})
    if "follow" in task:
        return json.dumps({"follow_ups": []})
    if "query" in task:
        return json.dumps({"queries": []})
    return ""

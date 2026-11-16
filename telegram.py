"""Telegram notifier. TG_TOKEN from env only; owner chat from owner.json. Owner-locked."""
import json, httpx
from config import secret, OWNER_FILE

def owner_chat_id() -> int | None:
    try: return json.loads(OWNER_FILE.read_text())["owner_chat_id"]
    except Exception: return None

async def send(text: str, buttons: list[list[dict]] | None = None, retries: int = 3) -> bool:
    token, chat = secret("TG_TOKEN"), owner_chat_id()
    if not token or not chat: return False
    body = {"chat_id": chat, "text": text, "disable_web_page_preview": True}
    if buttons: body["reply_markup"] = {"inline_keyboard": buttons}
    async with httpx.AsyncClient(timeout=10) as cl:
        for _ in range(retries):
            try:
                r = await cl.post(f"https://api.telegram.org/bot{token}/sendMessage", json=body)
                if r.status_code == 200 and r.json().get("ok"): return True
            except httpx.HTTPError:
                pass
    return False

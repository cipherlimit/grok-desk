"""Telegram notifier. TG_TOKEN from env only; owner chat from owner.json. Owner-locked.
Also a minimal callback poller for the agents' buttons (Bought / Skip / Sold) only:
no command set yet (that is a later step)."""
import asyncio, json, logging
import httpx
from config import secret, OWNER_FILE, LOG_DIR

log = logging.getLogger("telegram")
if not log.handlers:
    h = logging.FileHandler(LOG_DIR / "telegram.log"); h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    log.addHandler(h); log.setLevel(logging.INFO)

def owner_chat_id() -> int | None:
    try: return json.loads(OWNER_FILE.read_text())["owner_chat_id"]
    except Exception: return None

def _api(method):   # the token is in the URL: never log this string
    return f"https://api.telegram.org/bot{secret('TG_TOKEN')}/{method}"

async def send(text: str, buttons: list[list[dict]] | None = None, retries: int = 3) -> bool:
    """1 try + [3] retries. Returns False when Telegram stays down."""
    if not secret("TG_TOKEN") or not owner_chat_id(): return False
    body = {"chat_id": owner_chat_id(), "text": text, "disable_web_page_preview": True}
    if buttons: body["reply_markup"] = {"inline_keyboard": buttons}
    async with httpx.AsyncClient(timeout=10) as cl:
        for i in range(1 + retries):
            try:
                r = await cl.post(_api("sendMessage"), json=body)
                if r.status_code == 200 and r.json().get("ok"):
                    log.info("sent: %s", text.replace("\n", " | ")); return True
                log.warning("send HTTP %s (try %d)", r.status_code, i + 1)
            except httpx.HTTPError as e:
                log.warning("send error %s (try %d)", type(e).__name__, i + 1)
            if i < retries: await asyncio.sleep(1)
    log.error("push failed: %s", text.replace("\n", " | "))
    return False

def url_button(text, url): return {"text": text, "url": url}
def cb_button(text, data): return {"text": text, "callback_data": data[:64]}

async def poll_callbacks(bus):
    """Routes owner taps: bought:/skip:<mint> -> bus cb.executor, sold:<position id> -> bus cb.risk."""
    if not secret("TG_TOKEN"): return
    offset = None
    async with httpx.AsyncClient(timeout=40) as cl:
        while True:
            try:
                params = {"timeout": 30, "allowed_updates": json.dumps(["callback_query"])}
                if offset: params["offset"] = offset
                r = await cl.get(_api("getUpdates"), params=params)
                for u in (r.json().get("result") or []) if r.status_code == 200 else []:
                    offset = u["update_id"] + 1
                    q = u.get("callback_query")
                    if not q: continue
                    chat = ((q.get("message") or {}).get("chat") or {}).get("id")
                    await cl.post(_api("answerCallbackQuery"), json={"callback_query_id": q["id"]})
                    if chat != owner_chat_id(): continue
                    action, _, arg = (q.get("data") or "").partition(":")
                    log.info("callback %s %s", action, arg)
                    if action in ("bought", "skip"): await bus.put("cb.executor", {"action": action, "mint": arg})
                    elif action == "sold": await bus.put("cb.risk", {"action": "sold", "position_id": arg})
                if r.status_code != 200: await asyncio.sleep(5)
            except Exception as e:
                log.warning("getUpdates error %s", type(e).__name__); await asyncio.sleep(5)

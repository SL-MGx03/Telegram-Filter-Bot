import re
from telethon import TelegramClient
from telethon.tl.types import Message as TLMessage

from config import TG_API_ID, TG_API_HASH, TG_SESSION_NAME

telethon_client = None

def parse_tg_link(text: str):
    if not text:
        return None
    text = text.strip()
    m1 = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)$", text)
    if m1:
        return {"type": "public", "chat": m1.group(1), "msg_id": int(m1.group(2))}
    m2 = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", text)
    if m2:
        return {"type": "private", "chat": int(m2.group(1)), "msg_id": int(m2.group(2))}
    return None

async def init_telethon():
    global telethon_client
    if TG_API_ID and TG_API_HASH:
        telethon_client = TelegramClient(TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH)
        await telethon_client.start()

def get_client():
    return telethon_client

async def fetch_message_by_link(link: str):
    client = get_client()
    if not client:
        return None, "Telethon is not configured (TG_API_ID/TG_API_HASH missing)."

    parsed = parse_tg_link(link)
    if not parsed:
        return None, "Invalid telegram link format."

    try:
        if parsed["type"] == "public":
            entity = parsed["chat"]
            msg_id = parsed["msg_id"]
        else:
            entity = int(f"-100{parsed['chat']}")
            msg_id = parsed["msg_id"]

        msg: TLMessage = await client.get_messages(entity, ids=msg_id)
        if not msg:
            return None, "Message not found."
        if not msg.media:
            return None, "Message has no media."
        return msg, None
    except Exception as e:
        return None, f"Telethon fetch failed: {e}"

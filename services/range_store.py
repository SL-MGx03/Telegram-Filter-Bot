import logging
from datetime import datetime

import database as dbase
from add_flow import parse_link
from utils.helpers import make_source_link, next_simple_id, build_deep_link
import services.telethon_client as tc

logger = logging.getLogger(__name__)


async def save_range_by_links(begin_link: str, end_link: str, bot_username: str, uid: int):
    if not tc.telethon_client:
        return {"saved": 0, "scanned": 0, "error": "Telethon unavailable for range mode."}

    b = parse_link(begin_link)
    e = parse_link(end_link)
    if not b or not e:
        return {"saved": 0, "scanned": 0, "error": "Invalid begin/end link."}

    if b["type"] != e["type"] or str(b["chat"]) != str(e["chat"]):
        return {"saved": 0, "scanned": 0, "error": "Begin and end must be from same chat."}

    start_id, end_id = b["msg_id"], e["msg_id"]
    if start_id > end_id:
        start_id, end_id = end_id, start_id

    entity = b["chat"] if b["type"] == "public" else int(f"-100{b['chat']}")
    scanned = 0
    saved = 0

    for mid in range(start_id, end_id + 1):
        scanned += 1
        try:
            m = await tc.telethon_client.get_messages(entity, ids=mid)
            if not m or not m.media:
                continue

            kind = "unknown"
            if m.photo:
                kind = "photo"
            elif m.video:
                kind = "video"
            elif m.document:
                kind = "document"
            if kind == "unknown":
                continue

            chat = await m.get_chat()
            source_chat_id = getattr(chat, "id", None)
            source_chat_username = getattr(chat, "username", None)
            source_chat_title = getattr(chat, "title", None)
            source_link = make_source_link(source_chat_username, source_chat_id, m.id)

            batch_no = dbase.next_batch_no()
            item_id = next_simple_id()
            deep_link = build_deep_link(bot_username, item_id)

            doc = {
                "item_id": item_id,
                "batch_no": batch_no,
                "deep_link": deep_link,
                "media_kind": kind,
                "file_id": None,
                "file_unique_id": None,
                "file_name": getattr(getattr(m, "file", None), "name", None),
                "mime_type": getattr(getattr(m, "file", None), "mime_type", None),
                "file_size": getattr(getattr(m, "file", None), "size", None),
                "caption": m.message,
                "source": "range_telethon",
                "source_chat_id": source_chat_id,
                "source_chat_username": source_chat_username,
                "source_chat_title": source_chat_title,
                "source_message_id": m.id,
                "source_link": source_link,
                "added_by": uid,
                "created_at": datetime.utcnow(),
            }
            dbase.media_col.insert_one(doc)
            dbase.update_batch_count(batch_no, +1)
            saved += 1
        except Exception:
            continue

    return {"saved": saved, "scanned": scanned, "error": None}

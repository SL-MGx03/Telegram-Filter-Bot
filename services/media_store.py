from datetime import datetime

import database as dbase
from utils.helpers import detect_kind, extract_file_info, make_source_link, next_simple_id, build_deep_link


async def save_one_media(msg, bot_username: str, uid: int):
    kind = detect_kind(msg)
    if kind == "unknown":
        return None, "Unsupported media."

    file_id, fuid, fname, mime, fsize = extract_file_info(msg)

    source_chat_id = msg.chat_id
    source_chat_username = getattr(msg.chat, "username", None)
    source_chat_title = getattr(msg.chat, "title", None)
    source_msg_id = msg.message_id

    if getattr(msg, "forward_origin", None) and getattr(msg.forward_origin, "sender_chat", None):
        sc = msg.forward_origin.sender_chat
        source_chat_id = getattr(sc, "id", source_chat_id)
        source_chat_username = getattr(sc, "username", source_chat_username)
        source_chat_title = getattr(sc, "title", source_chat_title)
        if hasattr(msg.forward_origin, "message_id"):
            source_msg_id = msg.forward_origin.message_id

    source_link = make_source_link(source_chat_username, source_chat_id, source_msg_id)

    batch_no = dbase.next_batch_no()
    item_id = next_simple_id()
    deep_link = build_deep_link(bot_username, item_id)

    doc = {
        "item_id": item_id,
        "batch_no": batch_no,
        "deep_link": deep_link,
        "media_kind": kind,
        "file_id": file_id,
        "file_unique_id": fuid,
        "file_name": fname,
        "mime_type": mime,
        "file_size": fsize,
        "caption": msg.caption,
        "source": "forward_or_upload",
        "source_chat_id": source_chat_id,
        "source_chat_username": source_chat_username,
        "source_chat_title": source_chat_title,
        "source_message_id": source_msg_id,
        "source_link": source_link,
        "added_by": uid,
        "created_at": datetime.utcnow(),
    }
    dbase.media_col.insert_one(doc)
    dbase.update_batch_count(batch_no, +1)
    return doc, None

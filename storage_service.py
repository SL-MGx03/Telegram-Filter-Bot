from datetime import datetime
from urllib.parse import quote
from bson import ObjectId
from pymongo import ASCENDING

from config import BATCH_SIZE
from db import media_col, batch_col, next_batch_no, update_batch_count
from telethon_service import fetch_message_by_link

def is_sudo(user_id: int, sudo_admins: set[int]) -> bool:
    return user_id in sudo_admins

def make_item_id():
    return str(ObjectId())

def make_source_link(chat_username, chat_id, message_id):
    if chat_username and message_id:
        return f"https://t.me/{chat_username}/{message_id}"
    if chat_id and message_id:
        cid = str(chat_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        return f"https://t.me/c/{cid}/{message_id}"
    return None

def build_deep_link(bot_username: str, item_id: str):
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"

def media_kind_from_ptb(msg):
    if msg.photo: return "photo"
    if msg.video: return "video"
    if msg.document: return "document"
    if msg.audio: return "audio"
    if msg.voice: return "voice"
    if msg.animation: return "animation"
    return "unknown"

def extract_file_info(msg):
    if msg.photo:
        p = msg.photo[-1]
        return p.file_id, p.file_unique_id, f"photo_{p.file_unique_id}.jpg", "image/jpeg", p.file_size
    if msg.video:
        v = msg.video
        return v.file_id, v.file_unique_id, (v.file_name or f"video_{v.file_unique_id}.mp4"), v.mime_type, v.file_size
    if msg.document:
        d = msg.document
        return d.file_id, d.file_unique_id, d.file_name, d.mime_type, d.file_size
    if msg.audio:
        a = msg.audio
        return a.file_id, a.file_unique_id, a.file_name, a.mime_type, a.file_size
    if msg.voice:
        v = msg.voice
        return v.file_id, v.file_unique_id, f"voice_{v.file_unique_id}.ogg", v.mime_type, v.file_size
    if msg.animation:
        a = msg.animation
        return a.file_id, a.file_unique_id, (a.file_name or f"animation_{a.file_unique_id}.mp4"), a.mime_type, a.file_size
    return None, None, None, None, None

async def save_from_forward_or_upload(msg, bot_username: str, added_by: int):
    kind = media_kind_from_ptb(msg)
    if kind == "unknown":
        return None, "No supported media in message."

    file_id, file_unique_id, file_name, mime_type, file_size = extract_file_info(msg)

    batch_no = next_batch_no(BATCH_SIZE)
    item_id = make_item_id()
    deep_link = build_deep_link(bot_username, item_id)

    # source metadata
    source_chat_id = msg.chat_id
    source_chat_username = getattr(msg.chat, "username", None)
    source_chat_title = getattr(msg.chat, "title", None)
    source_message_id = msg.message_id

    if getattr(msg, "forward_origin", None) and getattr(msg.forward_origin, "sender_chat", None):
        sc = msg.forward_origin.sender_chat
        source_chat_id = getattr(sc, "id", source_chat_id)
        source_chat_username = getattr(sc, "username", source_chat_username)
        source_chat_title = getattr(sc, "title", source_chat_title)
        if hasattr(msg.forward_origin, "message_id"):
            source_message_id = msg.forward_origin.message_id

    source_link = make_source_link(source_chat_username, source_chat_id, source_message_id)

    doc = {
        "item_id": item_id,
        "batch_no": batch_no,
        "deep_link": deep_link,
        "media_kind": kind,
        "file_id": file_id,
        "file_unique_id": file_unique_id,
        "file_name": file_name,
        "mime_type": mime_type,
        "file_size": file_size,
        "caption": msg.caption,
        "source": "forward_or_upload",
        "source_chat_id": source_chat_id,
        "source_chat_username": source_chat_username,
        "source_chat_title": source_chat_title,
        "source_message_id": source_message_id,
        "source_link": source_link,
        "added_by": added_by,
        "created_at": datetime.utcnow(),
    }

    media_col.insert_one(doc)
    update_batch_count(batch_no, +1)
    return doc, None

async def save_from_telegram_link(link: str, bot_username: str, added_by: int):
    tl_msg, err = await fetch_message_by_link(link)
    if err:
        return None, err

    kind = "photo" if tl_msg.photo else "video" if tl_msg.video else "document" if tl_msg.document else "unknown"
    src_chat = await tl_msg.get_chat()

    source_chat_id = getattr(src_chat, "id", None)
    source_chat_username = getattr(src_chat, "username", None)
    source_chat_title = getattr(src_chat, "title", None)
    source_message_id = tl_msg.id
    source_link = make_source_link(source_chat_username, source_chat_id, source_message_id)

    batch_no = next_batch_no(BATCH_SIZE)
    item_id = make_item_id()
    deep_link = build_deep_link(bot_username, item_id)

    doc = {
        "item_id": item_id,
        "batch_no": batch_no,
        "deep_link": deep_link,
        "media_kind": kind,
        "file_id": None,  # telethon message may not map to bot file_id directly
        "file_unique_id": None,
        "file_name": getattr(getattr(tl_msg, "file", None), "name", None),
        "mime_type": getattr(getattr(tl_msg, "file", None), "mime_type", None),
        "file_size": getattr(getattr(tl_msg, "file", None), "size", None),
        "caption": tl_msg.message,
        "source": "link_telethon",
        "source_chat_id": source_chat_id,
        "source_chat_username": source_chat_username,
        "source_chat_title": source_chat_title,
        "source_message_id": source_message_id,
        "source_link": source_link,
        "added_by": added_by,
        "created_at": datetime.utcnow(),
    }

    media_col.insert_one(doc)
    update_batch_count(batch_no, +1)
    return doc, None

def get_item(item_id: str):
    return media_col.find_one({"item_id": item_id})

def remove_item(item_id: str):
    doc = media_col.find_one({"item_id": item_id})
    if not doc:
        return False, None
    media_col.delete_one({"item_id": item_id})
    update_batch_count(doc["batch_no"], -1)
    return True, doc

def get_batch_items(batch_no: int):
    return list(media_col.find({"batch_no": batch_no}).sort("created_at", ASCENDING))

def export_all():
    batches = list(batch_col.find({}).sort("batch_no", ASCENDING))
    items = list(media_col.find({}, {"_id": 0}).sort([("batch_no", ASCENDING), ("created_at", ASCENDING)]))
    for x in items:
        if isinstance(x.get("created_at"), datetime):
            x["created_at"] = x["created_at"].isoformat() + "Z"
    return {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batches),
        "total_items": len(items),
        "batches": batches,
        "items": items
    }

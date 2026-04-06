from urllib.parse import quote

import database as dbase
from config import SUDO_ADMINS


def is_sudo(uid: int) -> bool:
    return uid in SUDO_ADMINS


def next_simple_id():
    x = dbase.db["counters"].find_one_and_update(
        {"_id": "item_counter"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True
    )
    seq = x.get("seq", 1)
    return f"a{seq}"


def make_source_link(chat_username, chat_id, msg_id):
    if chat_username and msg_id:
        return f"https://t.me/{chat_username}/{msg_id}"
    if chat_id and msg_id:
        cid = str(chat_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        return f"https://t.me/c/{cid}/{msg_id}"
    return None


def build_deep_link(bot_username: str, item_id: str):
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"


def detect_kind(msg):
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


def link_from_msg(msg):
    return make_source_link(None, msg.chat_id, msg.message_id)

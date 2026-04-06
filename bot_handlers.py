import io
import json
import logging
from datetime import datetime
from urllib.parse import quote

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from telethon import TelegramClient

import database as dbase
from config import SUDO_ADMINS, TG_API_ID, TG_API_HASH, TG_SESSION_NAME
from add_flow import AddFlowManager, parse_link

logger = logging.getLogger(__name__)
telethon_client = None
add_flow = AddFlowManager()


# ---------- Telethon ----------
async def init_telethon():
    global telethon_client
    if not (TG_API_ID and TG_API_HASH and TG_SESSION_NAME):
        telethon_client = None
        logger.info("Telethon disabled.")
        return
    try:
        telethon_client = TelegramClient(TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH)
        await telethon_client.connect()
        if not await telethon_client.is_user_authorized():
            await telethon_client.disconnect()
            telethon_client = None
            logger.warning("Telethon session not authorized.")
            return
        logger.info("Telethon ready.")
    except Exception as e:
        telethon_client = None
        logger.warning(f"Telethon init failed: {e}")


# ---------- Helpers ----------
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
        if cid.startswith("-100"): cid = cid[4:]
        elif cid.startswith("-"): cid = cid[1:]
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


# ---------- Save single media ----------
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


# ---------- Save range by begin/end ----------
async def save_range_by_links(begin_link: str, end_link: str, bot_username: str, uid: int):
    if not telethon_client:
        return 0, "Telethon unavailable for range mode."

    b = parse_link(begin_link)
    e = parse_link(end_link)
    if not b or not e:
        return 0, "Invalid begin/end link."

    if b["type"] != e["type"] or str(b["chat"]) != str(e["chat"]):
        return 0, "Begin and end must be from same chat."

    start_id, end_id = b["msg_id"], e["msg_id"]
    if start_id > end_id:
        start_id, end_id = end_id, start_id

    entity = b["chat"] if b["type"] == "public" else int(f"-100{b['chat']}")
    saved = 0

    for mid in range(start_id, end_id + 1):
        try:
            m = await telethon_client.get_messages(entity, ids=mid)
            if not m or not m.media:
                continue

            kind = "photo" if m.photo else "video" if m.video else "document" if m.document else "unknown"
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
                "file_id": None,  # telethon range mode metadata/link save
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

    return saved, None


# ---------- Commands ----------
HELP_TEXT = """
<b>Commands</b>
/start
/help
/add (sudo): asks BEGIN then END link (range mode)
/addoff (sudo): cancel add mode
/get &lt;item_id&gt;
/send &lt;batch_no&gt;
/all
/remove &lt;item_id&gt; (sudo)
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if context.args and context.args[0].startswith("get_"):
        item_id = context.args[0][4:]
        doc = dbase.media_col.find_one({"item_id": item_id})
        if not doc:
            await update.message.reply_text("Item not found.")
            return
        await update.message.reply_text(
            f"ID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nSource: {doc.get('source_link') or 'N/A'}",
            parse_mode=ParseMode.HTML
        )
        return
    await update.message.reply_text("Welcome. Use /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /add sudo-only.")
        return
    await add_flow.start(uid, update.message.reply_text)

async def addoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /addoff sudo-only.")
        return
    await add_flow.cancel(uid, update.message.reply_text, "✅ Add cancelled.")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /remove sudo-only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <item_id>")
        return
    item_id = context.args[0].strip()
    doc = dbase.media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text("Item not found.")
        return
    dbase.media_col.delete_one({"item_id": item_id})
    dbase.update_batch_count(doc["batch_no"], -1)
    await update.message.reply_text(f"✅ Removed {item_id}")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <item_id>")
        return
    item_id = context.args[0].strip()
    doc = dbase.media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text("Item not found.")
        return
    text = f"ID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nType: <b>{doc.get('media_kind')}</b>\nSource: {doc.get('source_link') or 'N/A'}"
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return
    b = int(context.args[0])
    items = list(dbase.media_col.find({"batch_no": b}).sort("created_at", 1))
    if not items:
        await update.message.reply_text("No items.")
        return
    for d in items:
        t = f"{d['item_id']} | {d.get('media_kind')} | {d.get('source_link') or 'N/A'}"
        if d.get("file_id") and d.get("media_kind") == "photo":
            await update.message.reply_photo(d["file_id"], caption=t)
        elif d.get("file_id") and d.get("media_kind") == "video":
            await update.message.reply_video(d["file_id"], caption=t)
        elif d.get("file_id") and d.get("media_kind") == "document":
            await update.message.reply_document(d["file_id"], caption=t)
        else:
            await update.message.reply_text(t)

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batches = list(dbase.batch_col.find({}).sort("batch_no", 1))
    items = list(dbase.media_col.find({}, {"_id": 0}).sort([("batch_no", 1), ("created_at", 1)]))
    for x in items:
        if isinstance(x.get("created_at"), datetime):
            x["created_at"] = x["created_at"].isoformat() + "Z"
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batches),
        "total_items": len(items),
        "batches": batches,
        "items": items
    }
    bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    bio.name = "all_batches.json"
    await update.message.reply_document(bio)


# ---------- message handlers ----------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    uid = update.effective_user.id
    if not is_sudo(uid):
        return

    st = add_flow.get_state(uid)
    if not st.active:
        return

    # In add flow: media can be begin marker (or direct single save once begin/end done)
    if st.step == "begin":
        # begin by forwarded media message id reference
        fake_begin = f"https://t.me/c/{str(msg.chat_id).replace('-100','').replace('-','')}/{msg.message_id}"
        st.begin_link = fake_begin
        st.step = "end"
        st.retry_count = 0
        await msg.reply_text("✅ Begin media received. Now send END telegram link.")
        return

    if st.step == "end":
        await msg.reply_text("❌ END must be a telegram link.")
        return

    # if collect state and user sends media directly, save single too
    me = await context.bot.get_me()
    doc, err = await save_one_media(msg, me.username, uid)
    if err:
        await msg.reply_text(f"❌ {err}")
        return
    await msg.reply_text(f"✅ Saved {doc['item_id']} in batch {doc['batch_no']}.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    uid = update.effective_user.id
    text = msg.text.strip()

    # handle /add flow steps
    st = add_flow.get_state(uid)
    if st.active and is_sudo(uid):
        res = await add_flow.handle_text(uid, text, msg.reply_text)
        if res.get("ready"):
            me = await context.bot.get_me()
            total, err = await save_range_by_links(res["begin_link"], res["end_link"], me.username, uid)
            if err:
                await msg.reply_text(f"❌ {err}")
                await add_flow.cancel(uid)
                return
            await msg.reply_text(f"✅ Completed. Saved {total} media items. Batched by 50 automatically.")
            await add_flow.cancel(uid)
        return

    # optional: ignore text outside flow
    return

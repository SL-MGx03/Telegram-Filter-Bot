import io
import json
import re
import logging
from datetime import datetime
from urllib.parse import quote
from bson import ObjectId

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from telethon import TelegramClient
from telethon.tl.types import Message as TLMessage

import database as dbase
from config import SUDO_ADMINS, TG_API_ID, TG_API_HASH, TG_SESSION_NAME


logger = logging.getLogger(__name__)
telethon_client = None


# ---------- Telethon ----------
async def init_telethon():
    """
    Railway-safe Telethon init:
    - Does NOT trigger interactive OTP login in headless environment.
    - Only uses existing authorized session.
    """
    global telethon_client

    if not (TG_API_ID and TG_API_HASH and TG_SESSION_NAME):
        telethon_client = None
        logger.info("Telethon disabled (missing TG_API_ID/TG_API_HASH/TG_SESSION_NAME).")
        return

    try:
        telethon_client = TelegramClient(TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH)
        await telethon_client.connect()

        authorized = await telethon_client.is_user_authorized()
        if not authorized:
            logger.warning("Telethon session not authorized. Link fetch via Telethon disabled.")
            await telethon_client.disconnect()
            telethon_client = None
            return

        logger.info("Telethon authorized and ready.")
    except Exception as e:
        logger.warning(f"Telethon init failed, disabled: {e}")
        telethon_client = None


# ---------- Helpers ----------
def is_sudo(user_id: int) -> bool:
    return user_id in SUDO_ADMINS

def make_item_id():
    return str(ObjectId())

def parse_tg_link(text: str):
    # public:  https://t.me/channelusername/123
    # private: https://t.me/c/1234567890/123
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

def build_deep_link(bot_username: str, item_id: str) -> str:
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"

def make_source_link(chat_username: str | None, chat_id: int | None, message_id: int | None):
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
        x = msg.document
        return x.file_id, x.file_unique_id, x.file_name, x.mime_type, x.file_size
    if msg.audio:
        x = msg.audio
        return x.file_id, x.file_unique_id, x.file_name, x.mime_type, x.file_size
    if msg.voice:
        x = msg.voice
        return x.file_id, x.file_unique_id, f"voice_{x.file_unique_id}.ogg", x.mime_type, x.file_size
    if msg.animation:
        x = msg.animation
        return x.file_id, x.file_unique_id, (x.file_name or f"animation_{x.file_unique_id}.mp4"), x.mime_type, x.file_size
    return None, None, None, None, None

async def save_from_forward_or_media(msg, bot_username: str, user_id: int):
    kind = detect_kind(msg)
    if kind == "unknown":
        return None, "Unsupported media."

    file_id, file_unique_id, file_name, mime_type, file_size = extract_file_info(msg)

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

    batch_no = dbase.next_batch_no()
    item_id = make_item_id()
    deep_link = build_deep_link(bot_username, item_id)

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

        "added_by": user_id,
        "created_at": datetime.utcnow(),
    }

    dbase.media_col.insert_one(doc)
    dbase.update_batch_count(batch_no, +1)
    return doc, None

async def save_from_link(link: str, bot_username: str, user_id: int):
    if not telethon_client:
        return None, "Telethon session unavailable. Configure & authorize Telethon session first."

    parsed = parse_tg_link(link)
    if not parsed:
        return None, "Invalid telegram message link."

    try:
        if parsed["type"] == "public":
            entity = parsed["chat"]
        else:
            entity = int(f"-100{parsed['chat']}")

        tl_msg: TLMessage = await telethon_client.get_messages(entity, ids=parsed["msg_id"])
        if not tl_msg:
            return None, "Message not found."
        if not tl_msg.media:
            return None, "Message has no media."

        kind = "photo" if tl_msg.photo else "video" if tl_msg.video else "document" if tl_msg.document else "unknown"
        src_chat = await tl_msg.get_chat()

        source_chat_id = getattr(src_chat, "id", None)
        source_chat_username = getattr(src_chat, "username", None)
        source_chat_title = getattr(src_chat, "title", None)
        source_message_id = tl_msg.id
        source_link = make_source_link(source_chat_username, source_chat_id, source_message_id)

        batch_no = dbase.next_batch_no()
        item_id = make_item_id()
        deep_link = build_deep_link(bot_username, item_id)

        doc = {
            "item_id": item_id,
            "batch_no": batch_no,
            "deep_link": deep_link,

            "media_kind": kind,
            "file_id": None,
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

            "added_by": user_id,
            "created_at": datetime.utcnow(),
        }

        dbase.media_col.insert_one(doc)
        dbase.update_batch_count(batch_no, +1)
        return doc, None

    except Exception as e:
        return None, f"Failed to fetch via Telethon: {e}"


# ---------- Commands ----------
HELP_TEXT = """
<b>Commands</b>
/start - Start bot
/help - Help
/add - (sudo) enter add mode
/addoff - (sudo) exit add mode
/get &lt;item_id&gt; - get one saved item
/send &lt;batch_no&gt; - send all items in batch
/all - export all batches/items JSON
/remove &lt;item_id&gt; - (sudo) remove one item
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    if context.args and context.args[0].startswith("get_"):
        item_id = context.args[0][4:]
        doc = dbase.media_col.find_one({"item_id": item_id})
        if not doc:
            await msg.reply_text("Item not found.")
            return
        await msg.reply_text(
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Batch: <b>{doc['batch_no']}</b>\n"
            f"Type: <b>{doc.get('media_kind', 'unknown')}</b>\n"
            f"Source: {doc.get('source_link') or 'N/A'}",
            parse_mode=ParseMode.HTML
        )
        return

    await msg.reply_text("Welcome. Use /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ /add is sudo-only.")
        return
    context.user_data["add_mode"] = True
    await update.message.reply_text("✅ Add mode ON.\nNow send Telegram message link OR forward/send media.")

async def addoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ /addoff is sudo-only.")
        return
    context.user_data["add_mode"] = False
    await update.message.reply_text("✅ Add mode OFF.")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_sudo(update.effective_user.id):
        await update.message.reply_text("❌ /remove is sudo-only.")
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

    text = (
        f"ID: <code>{doc['item_id']}</code>\n"
        f"Batch: <b>{doc['batch_no']}</b>\n"
        f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
        f"File: <code>{doc.get('file_name') or 'N/A'}</code>\n"
        f"Source: {doc.get('source_link') or 'N/A'}\n"
        f"DeepLink: {doc.get('deep_link')}"
    )

    if doc.get("file_id"):
        kind = doc.get("media_kind")
        if kind == "photo":
            await update.message.reply_photo(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
        elif kind == "video":
            await update.message.reply_video(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
        elif kind == "document":
            await update.message.reply_document(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return

    batch_no = int(context.args[0])
    items = list(dbase.media_col.find({"batch_no": batch_no}).sort("created_at", 1))
    if not items:
        await update.message.reply_text("No items in this batch.")
        return

    await update.message.reply_text(f"Sending {len(items)} items from batch {batch_no} ...")
    for doc in items:
        line = (
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
            f"File: <code>{doc.get('file_name') or 'N/A'}</code>\n"
            f"Source: {doc.get('source_link') or 'N/A'}"
        )
        if doc.get("file_id"):
            try:
                kind = doc.get("media_kind")
                if kind == "photo":
                    await update.message.reply_photo(doc["file_id"], caption=line, parse_mode=ParseMode.HTML)
                elif kind == "video":
                    await update.message.reply_video(doc["file_id"], caption=line, parse_mode=ParseMode.HTML)
                elif kind == "document":
                    await update.message.reply_document(doc["file_id"], caption=line, parse_mode=ParseMode.HTML)
                else:
                    await update.message.reply_text(line, parse_mode=ParseMode.HTML)
            except Exception:
                await update.message.reply_text(line, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(line, parse_mode=ParseMode.HTML)

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batches = list(dbase.batch_col.find({}).sort("batch_no", 1))
    items = list(dbase.media_col.find({}, {"_id": 0}).sort([("batch_no", 1), ("created_at", 1)]))

    for x in items:
        if isinstance(x.get("created_at"), datetime):
            x["created_at"] = x["created_at"].isoformat() + "Z"
    for b in batches:
        if isinstance(b.get("created_at"), datetime):
            b["created_at"] = b["created_at"].isoformat() + "Z"
        if isinstance(b.get("updated_at"), datetime):
            b["updated_at"] = b["updated_at"].isoformat() + "Z"

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batches),
        "total_items": len(items),
        "batches": batches,
        "items": items
    }

    bio = io.BytesIO(json.dumps(payload, indent=2).encode("utf-8"))
    bio.name = "all_batches.json"
    await update.message.reply_document(document=bio, caption="All batches/items export.")


# ---------- Message handlers ----------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    if not context.user_data.get("add_mode", False):
        return
    if not is_sudo(update.effective_user.id):
        await msg.reply_text("❌ Not allowed.")
        return

    me = await context.bot.get_me()
    doc, err = await save_from_forward_or_media(msg, me.username, update.effective_user.id)
    if err:
        await msg.reply_text(f"❌ {err}")
        return

    await msg.reply_text(
        f"✅ Saved.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
        parse_mode=ParseMode.HTML
    )

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    if not context.user_data.get("add_mode", False):
        return
    if not is_sudo(update.effective_user.id):
        await msg.reply_text("❌ Not allowed.")
        return

    link = msg.text.strip()
    if not parse_tg_link(link):
        await msg.reply_text("Send valid telegram message link:\nhttps://t.me/<username>/<id>\nor https://t.me/c/<id>/<msg_id>")
        return

    me = await context.bot.get_me()
    doc, err = await save_from_link(link, me.username, update.effective_user.id)
    if err:
        await msg.reply_text(f"❌ {err}")
        return

    await msg.reply_text(
        f"✅ Saved from link.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
        parse_mode=ParseMode.HTML
    )

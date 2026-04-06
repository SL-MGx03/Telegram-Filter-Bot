import os
import re
import io
import json
import asyncio
import logging
from datetime import datetime
from urllib.parse import quote

from dotenv import load_dotenv
from pymongo import MongoClient, ASCENDING, DESCENDING
from bson import ObjectId

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

# Optional Telethon (for old/private resolving)
from telethon import TelegramClient
from telethon.tl.types import Message as TLMessage


# ----------------------------
# ENV / CONFIG
# ----------------------------
load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "telegram_deeplink_bot").strip()

SUDO_ADMINS = set()
for x in os.getenv("SUDO_ADMINS", "").split(","):
    x = x.strip()
    if x.isdigit():
        SUDO_ADMINS.add(int(x))

TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_NAME = os.getenv("TG_SESSION_NAME", "railway_user_session").strip()

BATCH_SIZE = 50

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is required.")

# ----------------------------
# LOGGING
# ----------------------------
logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("deeplink-bot")


# ----------------------------
# DB
# ----------------------------
mongo_client = MongoClient(MONGODB_URI)
db = mongo_client[MONGODB_DB]
media_col = db["media_items"]
batch_col = db["batches"]

media_col.create_index([("item_id", ASCENDING)], unique=True)
media_col.create_index([("batch_no", ASCENDING)])
media_col.create_index([("created_at", DESCENDING)])
batch_col.create_index([("batch_no", ASCENDING)], unique=True)


# ----------------------------
# TELETHON (optional)
# ----------------------------
telethon_client = None

async def init_telethon():
    global telethon_client
    if TG_API_ID and TG_API_HASH:
        try:
            api_id_int = int(TG_API_ID)
            telethon_client = TelegramClient(TG_SESSION_NAME, api_id_int, TG_API_HASH)
            await telethon_client.start()
            logger.info("Telethon client started.")
        except Exception as e:
            logger.warning(f"Telethon init failed: {e}")


# ----------------------------
# HELPERS
# ----------------------------
def is_sudo(user_id: int) -> bool:
    return user_id in SUDO_ADMINS

def make_item_id() -> str:
    # shorter readable id from ObjectId-like timestamp + random
    oid = ObjectId()
    return str(oid)

def next_batch_no() -> int:
    # find latest non-full batch else create next
    last_batch = batch_col.find_one(sort=[("batch_no", DESCENDING)])
    if not last_batch:
        batch_col.insert_one({
            "batch_no": 1,
            "count": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })
        return 1

    if last_batch.get("count", 0) < BATCH_SIZE:
        return last_batch["batch_no"]

    new_no = last_batch["batch_no"] + 1
    batch_col.insert_one({
        "batch_no": new_no,
        "count": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    return new_no

def increment_batch_count(batch_no: int, delta=1):
    batch_col.update_one(
        {"batch_no": batch_no},
        {"$inc": {"count": delta}, "$set": {"updated_at": datetime.utcnow()}},
        upsert=True
    )

def parse_tg_link(text: str):
    """
    Supports:
    - https://t.me/<public>/<msg_id>
    - https://t.me/c/<private_chat_id_without_100>/<msg_id>
    """
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

def media_kind_from_message(msg) -> str:
    if getattr(msg, "photo", None):
        return "photo"
    if getattr(msg, "video", None):
        return "video"
    if getattr(msg, "document", None):
        return "document"
    if getattr(msg, "audio", None):
        return "audio"
    if getattr(msg, "voice", None):
        return "voice"
    if getattr(msg, "animation", None):
        return "animation"
    return "unknown"

def build_private_deeplink(bot_username: str, item_id: str) -> str:
    # deep link to bot start payload
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"

def make_source_link(chat_username: str | None, chat_id: int | None, message_id: int | None):
    if chat_username and message_id:
        return f"https://t.me/{chat_username}/{message_id}"
    # For private channels where username doesn't exist:
    if chat_id and message_id:
        # Telegram private format for internal id:
        # remove -100 prefix if present
        cid = str(chat_id)
        if cid.startswith("-100"):
            cid = cid[4:]
        elif cid.startswith("-"):
            cid = cid[1:]
        return f"https://t.me/c/{cid}/{message_id}"
    return None


# ----------------------------
# INGEST LOGIC
# ----------------------------
async def save_media_item_from_ptb_message(msg, bot_username: str, added_by: int, source="forward_or_upload"):
    kind = media_kind_from_message(msg)

    file_id = None
    file_unique_id = None
    file_name = None
    mime_type = None
    file_size = None

    if msg.photo:
        largest = msg.photo[-1]
        file_id = largest.file_id
        file_unique_id = largest.file_unique_id
        file_size = largest.file_size
        file_name = f"photo_{file_unique_id}.jpg"
        mime_type = "image/jpeg"
    elif msg.video:
        v = msg.video
        file_id = v.file_id
        file_unique_id = v.file_unique_id
        file_size = v.file_size
        file_name = v.file_name or f"video_{file_unique_id}.mp4"
        mime_type = v.mime_type
    elif msg.document:
        d = msg.document
        file_id = d.file_id
        file_unique_id = d.file_unique_id
        file_size = d.file_size
        file_name = d.file_name
        mime_type = d.mime_type
    elif msg.audio:
        a = msg.audio
        file_id = a.file_id
        file_unique_id = a.file_unique_id
        file_size = a.file_size
        file_name = a.file_name
        mime_type = a.mime_type
    elif msg.voice:
        v = msg.voice
        file_id = v.file_id
        file_unique_id = v.file_unique_id
        file_size = v.file_size
        file_name = f"voice_{v.file_unique_id}.ogg"
        mime_type = v.mime_type
    elif msg.animation:
        a = msg.animation
        file_id = a.file_id
        file_unique_id = a.file_unique_id
        file_size = a.file_size
        file_name = a.file_name or f"animation_{a.file_unique_id}.mp4"
        mime_type = a.mime_type
    else:
        return None, "No supported media found in message."

    batch_no = next_batch_no()
    item_id = make_item_id()
    deep_link = build_private_deeplink(bot_username, item_id)

    chat = msg.forward_origin.sender_chat if getattr(msg, "forward_origin", None) and getattr(msg.forward_origin, "sender_chat", None) else msg.chat
    source_chat_id = getattr(chat, "id", None)
    source_chat_username = getattr(chat, "username", None)
    source_chat_title = getattr(chat, "title", None)
    source_message_id = msg.forward_origin.message_id if getattr(msg, "forward_origin", None) and hasattr(msg.forward_origin, "message_id") else msg.message_id

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
        "source": source,

        "source_chat_id": source_chat_id,
        "source_chat_username": source_chat_username,
        "source_chat_title": source_chat_title,
        "source_message_id": source_message_id,
        "source_link": source_link,

        "added_by": added_by,
        "created_at": datetime.utcnow(),
    }

    media_col.insert_one(doc)
    increment_batch_count(batch_no, 1)
    return doc, None


async def save_media_from_link_using_telethon(link: str, bot_username: str, added_by: int):
    if not telethon_client:
        return None, "Telethon user session is not configured. Add TG_API_ID/TG_API_HASH."

    parsed = parse_tg_link(link)
    if not parsed:
        return None, "Invalid Telegram message link format."

    try:
        if parsed["type"] == "public":
            entity = parsed["chat"]  # username
            msg_id = parsed["msg_id"]
        else:
            # private t.me/c/<id>/<msg_id> => real chat id is -100<id>
            entity = int(f"-100{parsed['chat']}")
            msg_id = parsed["msg_id"]

        tl_msg: TLMessage = await telethon_client.get_messages(entity, ids=msg_id)
        if not tl_msg:
            return None, "Message not found."
        if not tl_msg.media:
            return None, "Message has no media."

        # Export media details
        kind = "unknown"
        if tl_msg.photo:
            kind = "photo"
        elif tl_msg.video:
            kind = "video"
        elif tl_msg.document:
            kind = "document"

        batch_no = next_batch_no()
        item_id = make_item_id()
        deep_link = build_private_deeplink(bot_username, item_id)

        src_chat = await tl_msg.get_chat()
        source_chat_id = getattr(src_chat, "id", None)
        source_chat_username = getattr(src_chat, "username", None)
        source_chat_title = getattr(src_chat, "title", None)
        source_message_id = tl_msg.id

        source_link = make_source_link(source_chat_username, source_chat_id, source_message_id)

        # NOTE: We cannot always derive a bot file_id from telethon media directly.
        # We'll store access details so /get can provide source link, and optionally fetch/reupload in advanced mode.
        doc = {
            "item_id": item_id,
            "batch_no": batch_no,
            "deep_link": deep_link,

            "media_kind": kind,
            "file_id": None,  # not from bot context
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

            "telethon_entity": str(entity),
            "telethon_msg_id": msg_id,

            "added_by": added_by,
            "created_at": datetime.utcnow(),
        }

        media_col.insert_one(doc)
        increment_batch_count(batch_no, 1)
        return doc, None
    except Exception as e:
        return None, f"Failed to fetch via Telethon: {e}"


# ----------------------------
# COMMANDS
# ----------------------------
HELP_TEXT = """
<b>Commands</b>
/start - Start bot
/help - Help
/add - (sudo only) enter add mode, then send Telegram link or forward media
/get &lt;item_id&gt; - fetch one stored item
/send &lt;batch_no&gt; - send all items in a batch
/all - export all stored batch metadata
/remove &lt;item_id&gt; - (sudo only) remove an item
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome. Use /help.\nYou can store channel media deep links in batches of 50."
    )

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Not allowed. /add is sudo-only.")
        return
    context.user_data["add_mode"] = True
    await update.message.reply_text(
        "✅ Add mode enabled.\nSend Telegram message link OR forward/send a media message."
    )

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Not allowed. /remove is sudo-only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <item_id>")
        return

    item_id = context.args[0].strip()
    doc = media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text("Item not found.")
        return

    media_col.delete_one({"item_id": item_id})
    increment_batch_count(doc["batch_no"], -1)
    await update.message.reply_text(f"✅ Removed item {item_id} from batch {doc['batch_no']}.")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <item_id>")
        return
    item_id = context.args[0].strip()

    doc = media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text("Item not found.")
        return

    caption = (
        f"ID: <code>{doc['item_id']}</code>\n"
        f"Batch: <b>{doc['batch_no']}</b>\n"
        f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
        f"File: <code>{doc.get('file_name') or 'N/A'}</code>\n"
        f"Source: {doc.get('source_link') or 'N/A'}\n"
        f"DeepLink: {doc.get('deep_link')}\n"
    )

    if doc.get("file_id"):
        # Resend media by file_id
        kind = doc.get("media_kind")
        if kind == "photo":
            await update.message.reply_photo(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        elif kind == "video":
            await update.message.reply_video(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        elif kind == "document":
            await update.message.reply_document(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
    else:
        # no file_id (telethon-linked item)
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return

    batch_no = int(context.args[0])
    items = list(media_col.find({"batch_no": batch_no}).sort("created_at", ASCENDING))
    if not items:
        await update.message.reply_text("No items in this batch.")
        return

    await update.message.reply_text(f"Sending {len(items)} items from batch {batch_no}...")

    for doc in items:
        text = (
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Batch: <b>{doc['batch_no']}</b>\n"
            f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
            f"File: <code>{doc.get('file_name') or 'N/A'}</code>\n"
            f"Source: {doc.get('source_link') or 'N/A'}"
        )
        if doc.get("file_id"):
            kind = doc.get("media_kind")
            try:
                if kind == "photo":
                    await update.message.reply_photo(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
                elif kind == "video":
                    await update.message.reply_video(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
                elif kind == "document":
                    await update.message.reply_document(doc["file_id"], caption=text, parse_mode=ParseMode.HTML)
                else:
                    await update.message.reply_text(text, parse_mode=ParseMode.HTML)
            except Exception:
                await update.message.reply_text(text, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batches = list(batch_col.find({}).sort("batch_no", ASCENDING))
    items = list(media_col.find({}, {
        "_id": 0,
        "item_id": 1,
        "batch_no": 1,
        "media_kind": 1,
        "file_name": 1,
        "mime_type": 1,
        "file_size": 1,
        "source_link": 1,
        "deep_link": 1,
        "created_at": 1,
    }).sort([("batch_no", ASCENDING), ("created_at", ASCENDING)]))

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batches),
        "total_items": len(items),
        "batches": batches,
        "items": [
            {**x, "created_at": x["created_at"].isoformat() + "Z"} if isinstance(x.get("created_at"), datetime) else x
            for x in items
        ]
    }

    b = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    b.name = "all_batches.json"
    await update.message.reply_document(document=b, caption="All batch/item data exported.")

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    Handles:
    - /add mode with text link
    - deep-link start payload: /start get_<item_id>
    """
    msg = update.message
    if not msg:
        return

    uid = update.effective_user.id

    # If /start payload handled here for safety:
    if msg.text and msg.text.startswith("/start "):
        payload = msg.text.split(" ", 1)[1].strip()
        if payload.startswith("get_"):
            item_id = payload[4:]
            doc = media_col.find_one({"item_id": item_id})
            if not doc:
                await msg.reply_text("Item not found.")
                return
            await msg.reply_text(
                f"Item <code>{item_id}</code>\nBatch: <b>{doc['batch_no']}</b>\nSource: {doc.get('source_link') or 'N/A'}",
                parse_mode=ParseMode.HTML
            )
            return

    add_mode = context.user_data.get("add_mode", False)
    if add_mode and msg.text:
        if not is_sudo(uid):
            await msg.reply_text("❌ Not allowed.")
            return

        link = msg.text.strip()
        if parse_tg_link(link):
            me = await context.bot.get_me()
            doc, err = await save_media_from_link_using_telethon(
                link=link,
                bot_username=me.username,
                added_by=uid
            )
            if err:
                await msg.reply_text(f"❌ {err}")
                return
            await msg.reply_text(
                f"✅ Saved from link.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
                parse_mode=ParseMode.HTML
            )
        else:
            await msg.reply_text("Send valid Telegram message link (t.me/... or t.me/c/...).")


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    uid = update.effective_user.id

    add_mode = context.user_data.get("add_mode", False)
    if not add_mode:
        return

    if not is_sudo(uid):
        await msg.reply_text("❌ Not allowed.")
        return

    me = await context.bot.get_me()
    doc, err = await save_media_item_from_ptb_message(
        msg=msg, bot_username=me.username, added_by=uid
    )
    if err:
        await msg.reply_text(f"❌ {err}")
        return

    await msg.reply_text(
        f"✅ Saved.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
        parse_mode=ParseMode.HTML
    )


async def post_init(app):
    await init_telethon()


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("all", all_cmd))

    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.ANIMATION,
        media_handler
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    logger.info("Bot running...")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

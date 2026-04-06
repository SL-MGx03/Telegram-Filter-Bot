"""
Enhanced Bot Handlers Module - Fixed Concurrency & Permanent Links
- Fixes: Atomic operations for concurrent message handling
- New: Permanent source_link storage
- New: Enhanced /get command for group retrieval
- New: Better media resend logic
"""

import io
import json
import logging
from datetime import datetime
from urllib.parse import quote

from telegram import Update, InputMediaPhoto, InputMediaVideo, InputMediaDocument
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from telethon import TelegramClient

import database as dbase
from config import SUDO_ADMINS, TG_API_ID, TG_API_HASH, TG_SESSION_NAME
from add_flow import AddFlowManager, parse_link
from addmode import start_mode, stop_mode, is_on, enqueue

logger = logging.getLogger(__name__)
telethon_client = None
add_flow = AddFlowManager()


# ---------- Telethon Client Setup ----------
async def init_telethon():
    """Initialize Telethon client for range mode"""
    global telethon_client
    if not (TG_API_ID and TG_API_HASH and TG_SESSION_NAME):
        telethon_client = None
        logger.info("Telethon disabled (missing credentials)")
        return
    try:
        telethon_client = TelegramClient(TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH)
        await telethon_client.connect()
        if not await telethon_client.is_user_authorized():
            await telethon_client.disconnect()
            telethon_client = None
            logger.warning("Telethon session not authorized")
            return
        logger.info("✅ Telethon ready for range mode")
    except Exception as e:
        telethon_client = None
        logger.warning(f"Telethon init failed: {e}")


# ---------- Helper Functions ----------
def is_sudo(uid: int) -> bool:
    """Check if user is admin"""
    return uid in SUDO_ADMINS

def make_source_link(chat_username: str, chat_id: int, msg_id: int) -> str:
    """Create permanent Telegram link (survives bot deletion)"""
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

def build_deep_link(bot_username: str, item_id: str) -> str:
    """Create deep link for item retrieval"""
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"

def detect_media_kind(msg) -> str:
    """Detect media type"""
    if msg.photo:
        return "photo"
    if msg.video:
        return "video"
    if msg.document:
        return "document"
    if msg.audio:
        return "audio"
    if msg.voice:
        return "voice"
    if msg.animation:
        return "animation"
    return "unknown"

def extract_file_info(msg) -> tuple:
    """Extract file_id, file_unique_id, name, mime, size"""
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

def link_from_message(msg) -> str:
    """Create permanent link from message"""
    cid = str(msg.chat_id).replace("-100", "").replace("-", "")
    return f"https://t.me/c/{cid}/{msg.message_id}"


# ---------- Save Single Media ----------
async def save_one_media(msg, bot_username: str, uid: int) -> tuple:
    """
    Save single forwarded/uploaded media
    IMPROVED: Now stores permanent source_link
    """
    kind = detect_media_kind(msg)
    if kind == "unknown":
        return None, "Unsupported media type"

    file_id, fuid, fname, mime, fsize = extract_file_info(msg)
    if not file_id:
        return None, "Could not extract file info"

    # Source tracking (important for recovery)
    source_chat_id = msg.chat_id
    source_chat_username = getattr(msg.chat, "username", None)
    source_chat_title = getattr(msg.chat, "title", None)
    source_msg_id = msg.message_id

    # Track forward origin
    if getattr(msg, "forward_origin", None) and getattr(msg.forward_origin, "sender_chat", None):
        sc = msg.forward_origin.sender_chat
        source_chat_id = getattr(sc, "id", source_chat_id)
        source_chat_username = getattr(sc, "username", source_chat_username)
        source_chat_title = getattr(sc, "title", source_chat_title)
        if hasattr(msg.forward_origin, "message_id"):
            source_msg_id = msg.forward_origin.message_id

    source_link = make_source_link(source_chat_username, source_chat_id, source_msg_id)

    # FIXED: Use atomic counter
    batch_no = dbase.get_next_batch_no()
    item_id = dbase.get_next_item_id()
    deep_link = build_deep_link(bot_username, item_id)

    doc = {
        "item_id": item_id,
        "batch_no": batch_no,
        "deep_link": deep_link,
        "media_kind": kind,
        "file_id": file_id,  # Temporary (bot-specific)
        "file_unique_id": fuid,  # Permanent identifier
        "file_name": fname,
        "mime_type": mime,
        "file_size": fsize,
        "caption": msg.caption,
        "source": "forward_or_upload",
        "source_chat_id": source_chat_id,
        "source_chat_username": source_chat_username,
        "source_chat_title": source_chat_title,
        "source_message_id": source_msg_id,
        "source_link": source_link,  # PERMANENT - survives bot deletion
        "added_by": uid,
        "created_at": datetime.utcnow(),
    }

    try:
        dbase.insert_media_item(doc)
        dbase.update_batch_count(batch_no, +1)
        return doc, None
    except Exception as e:
        logger.exception("Save failed: %s", e)
        return None, f"Database error: {str(e)}"


# ---------- Save Range via Telethon ----------
async def save_range_by_links(begin_link: str, end_link: str, bot_username: str, uid: int) -> dict:
    """
    Save range of messages from begin to end
    IMPROVED: Better error handling and atomic operations
    """
    if not telethon_client:
        return {"saved": 0, "scanned": 0, "error": "Telethon unavailable for range mode"}

    b = parse_link(begin_link)
    e = parse_link(end_link)
    if not b or not e:
        return {"saved": 0, "scanned": 0, "error": "Invalid begin/end links"}

    if b["type"] != e["type"] or str(b["chat"]) != str(e["chat"]):
        return {"saved": 0, "scanned": 0, "error": "Begin and end must be from same chat"}

    start_id, end_id = b["msg_id"], e["msg_id"]
    if start_id > end_id:
        start_id, end_id = end_id, start_id

    entity = b["chat"] if b["type"] == "public" else int(f"-100{b['chat']}")
    scanned = 0
    saved = 0
    errors = 0

    logger.info(f"Starting range scan: {start_id} to {end_id}")

    for mid in range(start_id, end_id + 1):
        scanned += 1
        try:
            m = await telethon_client.get_messages(entity, ids=mid)
            if not m or not m.media:
                continue

            kind = detect_media_kind(m) if hasattr(m, "photo") or hasattr(m, "video") else "unknown"
            if kind == "unknown":
                continue

            # Get chat info
            chat = await m.get_chat()
            source_chat_id = getattr(chat, "id", None)
            source_chat_username = getattr(chat, "username", None)
            source_chat_title = getattr(chat, "title", None)
            source_link = make_source_link(source_chat_username, source_chat_id, m.id)

            batch_no = dbase.get_next_batch_no()
            item_id = dbase.get_next_item_id()
            deep_link = build_deep_link(bot_username, item_id)

            doc = {
                "item_id": item_id,
                "batch_no": batch_no,
                "deep_link": deep_link,
                "media_kind": kind,
                "file_id": None,  # Can't get file_id from Telethon
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
                "source_link": source_link,  # PERMANENT link
                "added_by": uid,
                "created_at": datetime.utcnow(),
            }

            dbase.insert_media_item(doc)
            dbase.update_batch_count(batch_no, +1)
            saved += 1

        except Exception as e:
            errors += 1
            logger.warning(f"Error processing message {mid}: {e}")
            continue

    logger.info(f"Range complete: {saved}/{scanned} saved, {errors} errors")
    return {"saved": saved, "scanned": scanned, "error": None}


# ---------- Commands ----------
HELP_TEXT = """
<b>📚 Telegram Filter Bot - Commands</b>

<b>👤 Admin Commands:</b>
/add - Add by range (begin → end links)
/addoff - Cancel add operation
/addmode on|off - Bulk capture mode
/remove &lt;item_id&gt; - Delete item

<b>👥 Public Commands:</b>
/get &lt;item_id&gt; - Get item info
/get_batch &lt;batch_no&gt; - Get entire batch (50 files)
/send &lt;batch_no&gt; - Send batch media
/all - Download all as JSON

<b>📁 Batch System:</b>
Files are organized in groups of 50.
Each batch can be retrieved together.
Use /get_batch to see all files in a group.
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /start command"""
    if context.args and context.args[0].startswith("get_"):
        item_id = context.args[0][4:]
        doc = dbase.get_item_by_id(item_id)
        if not doc:
            await update.message.reply_text("❌ Item not found")
            return
        text = (
            f"<b>Item Information</b>\n"
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Batch: <b>{doc['batch_no']}</b>\n"
            f"Type: {doc.get('media_kind', 'unknown')}\n"
            f"Size: {doc.get('file_size', 'N/A')} bytes\n"
            f"Saved: {doc.get('created_at', 'N/A')}\n"
            f"<a href=\"{doc.get('source_link')}\">View Original</a>"
        )
        await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
        return
    
    await update.message.reply_text("Welcome to Telegram Filter Bot\nUse /help for commands")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle /help command"""
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Start add flow (admin only)"""
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Admin only")
        return
    await add_flow.start(uid, update.message.reply_text)

async def addoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Cancel add flow"""
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Admin only")
        return
    await add_flow.cancel(uid, update.message.reply_text, "✅ Add cancelled")

async def addmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Toggle bulk capture mode"""
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Admin only")
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /addmode on|off")
        return

    mode = context.args[0].lower()
    if mode == "on":
        await start_mode(uid, context.application, update.message.reply_text)
    else:
        await stop_mode(uid, update.message.reply_text)

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Remove item (admin only)"""
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ Admin only")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <item_id>")
        return
    
    item_id = context.args[0].strip()
    deleted = dbase.delete_media_item(item_id)
    if deleted:
        await update.message.reply_text(f"✅ Removed {item_id}")
    else:
        await update.message.reply_text("❌ Item not found")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get single item info"""
    if not context.args:
        await update.message.reply_text("Usage: /get <item_id>")
        return
    
    item_id = context.args[0].strip()
    doc = dbase.get_item_by_id(item_id)
    if not doc:
        await update.message.reply_text("❌ Item not found")
        return
    
    text = (
        f"<b>📄 Item Details</b>\n"
        f"ID: <code>{doc['item_id']}</code>\n"
        f"Batch: <b>{doc['batch_no']}</b>\n"
        f"Type: {doc.get('media_kind')}\n"
        f"Name: {doc.get('file_name', 'N/A')}\n"
        f"Size: {doc.get('file_size', 'N/A')} bytes\n"
        f"Source: <a href=\"{doc.get('source_link')}\">View</a>"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

async def get_batch_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Get entire batch info (NEW)"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /get_batch <batch_no>")
        return
    
    batch_no = int(context.args[0])
    items = dbase.get_items_in_batch(batch_no)
    
    if not items:
        await update.message.reply_text(f"❌ Batch {batch_no} is empty")
        return
    
    text = f"<b>📦 Batch {batch_no} ({len(items)} files)</b>\n\n"
    for item in items:
        text += f"• {item['item_id']} - {item.get('media_kind')} - {item.get('file_name', 'N/A')}\n"
    
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Send batch media (with media group support)"""
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return
    
    batch_no = int(context.args[0])
    items = dbase.get_items_in_batch(batch_no)
    
    if not items:
        await update.message.reply_text(f"❌ Batch {batch_no} has no items")
        return
    
    # Separate by type
    photos = [i for i in items if i.get("file_id") and i.get("media_kind") == "photo"]
    videos = [i for i in items if i.get("file_id") and i.get("media_kind") == "video"]
    docs = [i for i in items if i.get("file_id") and i.get("media_kind") == "document"]
    others = [i for i in items if not i.get("file_id")]
    
    # Send as media group (faster for bulk)
    if photos:
        media_group = [InputMediaPhoto(i["file_id"]) for i in photos[:10]]  # Max 10 per group
        if media_group:
            await update.message.reply_media_group(media_group)
    
    # Send videos
    for video in videos[:5]:  # Limit to avoid rate limit
        await update.message.reply_video(video["file_id"], caption=f"{video['item_id']}")
    
    # Send documents
    for doc in docs[:5]:
        await update.message.reply_document(doc["file_id"], caption=f"{doc['item_id']}")
    
    # Send others as links
    if others:
        text = f"<b>📦 Batch {batch_no} - Archived Items</b>\n"
        for item in others:
            text += f"• <a href=\"{item['source_link']}\">View {item['item_id']}</a>\n"
        await update.message.reply_text(text, parse_mode=ParseMode.HTML)

async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Export all items as JSON"""
    batches = dbase.get_all_batches(page_size=1000)
    items = dbase.search_items({}, page_size=10000)
    
    # Convert datetime to ISO format
    for item in items:
        if isinstance(item.get("created_at"), datetime):
            item["created_at"] = item["created_at"].isoformat() + "Z"
    
    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batches),
        "total_items": len(items),
        "batch_summary": [{"batch_no": b["batch_no"], "count": b.get("count", 0)} for b in batches],
        "items": items
    }
    
    bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    bio.name = "all_batches.json"
    
    await update.message.reply_document(
        bio,
        caption=f"📊 Total: {len(batches)} batches | {len(items)} items"
    )


# ---------- Message Handlers ----------
async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming media"""
    msg = update.message
    if not msg:
        return

    uid = update.effective_user.id
    if not is_sudo(uid):
        return

    me = await context.bot.get_me()

    # Priority: AddMode
    if is_on(uid):
        await enqueue(uid, msg, me.username, save_one_media)
        return

    st = add_flow.get_state(uid)
    if not st.active:
        return

    # BEGIN: accept media/forward
    if st.step == "begin":
        st.begin_link = link_from_message(msg)
        st.step = "end"
        st.retry_count = 0
        await msg.reply_text("✅ Begin received\nNow send END link or forward media")
        return

    # END: accept media/forward
    if st.step == "end":
        st.end_link = link_from_message(msg)
        await msg.reply_text("✅ Processing range...")
        result = await save_range_by_links(st.begin_link, st.end_link, me.username, uid)
        if result["error"]:
            await msg.reply_text(f"❌ {result['error']}")
        else:
            await msg.reply_text(
                f"✅ Scanned: {result['scanned']}\n"
                f"Saved: {result['saved']}\n"
                f"(Batch size: 50)"
            )
        await add_flow.cancel(uid)
        return

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Handle incoming text"""
    msg = update.message
    if not msg or not msg.text:
        return
    
    uid = update.effective_user.id
    text = msg.text.strip()

    st = add_flow.get_state(uid)
    if st.active and is_sudo(uid):
        res = await add_flow.handle_text(uid, text, msg.reply_text)
        if res.get("ready"):
            me = await context.bot.get_me()
            result = await save_range_by_links(res["begin_link"], res["end_link"], me.username, uid)
            if result["error"]:
                await msg.reply_text(f"❌ {result['error']}")
            else:
                await msg.reply_text(
                    f"✅ Scanned: {result['scanned']}\n"
                    f"Saved: {result['saved']}"
                )
            await add_flow.cancel(uid)

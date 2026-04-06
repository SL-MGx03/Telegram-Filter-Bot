"""
bot_handlers.py
~~~~~~~~~~~~~~~
All Telegram command handlers and media/text message handlers.

Commands
--------
/start [get_<id>]  – welcome or deep-link retrieval
/help              – usage guide
/add   (sudo)      – collect a range of messages via BEGIN/END links
/addoff (sudo)     – cancel an in-progress /add
/addmode on|off    – bulk streaming capture
/get <item_id>     – show metadata + deep link for one item
/send <batch_no>   – resend all media in a batch
/all               – send a JSON file with every batch & item
/remove <item_id>  – delete an item (sudo)
"""

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
from config import (
    SUDO_ADMINS,
    TG_API_ID,
    TG_API_HASH,
    TG_SESSION_NAME,
    ARCHIVE_CHANNEL_ID,
)
from add_flow import AddFlowManager, parse_link
from addmode import start_mode, stop_mode, is_on, enqueue

logger = logging.getLogger(__name__)

telethon_client: TelegramClient | None = None
add_flow = AddFlowManager()

# ────────────────────────────────────────────────────────────────
# Telethon initialisation (user-account for old / private channel messages)
# ────────────────────────────────────────────────────────────────


async def init_telethon():
    global telethon_client
    if not (TG_API_ID and TG_API_HASH):
        logger.info("Telethon disabled – TG_API_ID / TG_API_HASH not set.")
        return
    try:
        telethon_client = TelegramClient(
            TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH
        )
        await telethon_client.connect()
        if not await telethon_client.is_user_authorized():
            await telethon_client.disconnect()
            telethon_client = None
            logger.warning(
                "Telethon session not authorised – run auth_telethon.py first."
            )
            return
        me = await telethon_client.get_me()
        logger.info("Telethon ready as %s", getattr(me, "username", me.id))
    except Exception as exc:
        telethon_client = None
        logger.warning("Telethon init failed: %s", exc)


# ────────────────────────────────────────────────────────────────
# Utility helpers
# ────────────────────────────────────────────────────────────────


def is_sudo(uid: int) -> bool:
    return uid in SUDO_ADMINS


def make_source_link(chat_username, chat_id, msg_id) -> str | None:
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
    return f"https://t.me/{bot_username}?start=get_{quote(item_id)}"


def detect_kind(msg) -> str:
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
    """Returns (file_id, file_unique_id, file_name, mime_type, file_size)."""
    if msg.photo:
        p = msg.photo[-1]
        return (
            p.file_id,
            p.file_unique_id,
            f"photo_{p.file_unique_id}.jpg",
            "image/jpeg",
            p.file_size,
        )
    if msg.video:
        v = msg.video
        return (
            v.file_id,
            v.file_unique_id,
            v.file_name or f"video_{v.file_unique_id}.mp4",
            v.mime_type,
            v.file_size,
        )
    if msg.document:
        d = msg.document
        return (
            d.file_id,
            d.file_unique_id,
            d.file_name,
            d.mime_type,
            d.file_size,
        )
    if msg.audio:
        a = msg.audio
        return (
            a.file_id,
            a.file_unique_id,
            a.file_name or f"audio_{a.file_unique_id}.ogg",
            a.mime_type,
            a.file_size,
        )
    if msg.voice:
        v = msg.voice
        return (
            v.file_id,
            v.file_unique_id,
            f"voice_{v.file_unique_id}.ogg",
            v.mime_type,
            v.file_size,
        )
    if msg.animation:
        a = msg.animation
        return (
            a.file_id,
            a.file_unique_id,
            a.file_name or f"anim_{a.file_unique_id}.mp4",
            a.mime_type,
            a.file_size,
        )
    return None, None, None, None, None


def link_from_msg(msg) -> str:
    """Build a private-style t.me/c/ link from a bot-API message object."""
    cid = str(msg.chat_id).replace("-100", "").replace("-", "")
    return f"https://t.me/c/{cid}/{msg.message_id}"


# ────────────────────────────────────────────────────────────────
# Core save functions
# ────────────────────────────────────────────────────────────────


async def _archive_message(bot, msg) -> tuple[int | None, str | None]:
    """
    Copy the message into the archive channel immediately.
    Returns (archive_message_id, new_file_id_from_archive).

    Why copy_message instead of forward_message?
    forward_message shows "Forwarded from …" which leaks the source chat.
    copy_message sends a clean copy with no attribution.
    """
    if not ARCHIVE_CHANNEL_ID:
        return None, None
    try:
        archived = await bot.copy_message(
            chat_id=ARCHIVE_CHANNEL_ID,
            from_chat_id=msg.chat_id,
            message_id=msg.message_id,
        )

        archive_file_id = None
        if hasattr(archived, "photo") and archived.photo:
            archive_file_id = archived.photo[-1].file_id
        elif hasattr(archived, "video") and archived.video:
            archive_file_id = archived.video.file_id
        elif hasattr(archived, "document") and archived.document:
            archive_file_id = archived.document.file_id
        elif hasattr(archived, "audio") and archived.audio:
            archive_file_id = archived.audio.file_id
        elif hasattr(archived, "voice") and archived.voice:
            archive_file_id = archived.voice.file_id
        elif hasattr(archived, "animation") and archived.animation:
            archive_file_id = archived.animation.file_id

        return archived.message_id, archive_file_id
    except Exception as exc:
        logger.warning("Archive copy failed: %s", exc)
        return None, None


async def save_one_media(msg, bot_username: str, uid: int) -> tuple[dict | None, str | None]:
    """
    Save a single media message (forwarded or uploaded directly).

    Archive flow
    ------------
    1. Detect & extract metadata from the incoming message.
    2. copy_message → ARCHIVE_CHANNEL_ID  (permanent copy in our channel).
    3. Use the archive's file_id (tied to our channel) in the DB record.
       Falls back to the original file_id if archiving is disabled or fails.
    4. Insert into MongoDB.
    """
    kind = detect_kind(msg)
    if kind == "unknown":
        return None, "Unsupported media type."

    file_id, fuid, fname, mime, fsize = extract_file_info(msg)

    # ── extract original source from forward_origin ──
    source_chat_id = msg.chat_id
    source_chat_username = getattr(msg.chat, "username", None)
    source_chat_title = getattr(msg.chat, "title", None)
    source_msg_id = msg.message_id

    fo = getattr(msg, "forward_origin", None)
    if fo:
        sender_chat = getattr(fo, "sender_chat", None)
        if sender_chat:
            source_chat_id = getattr(sender_chat, "id", source_chat_id)
            source_chat_username = getattr(sender_chat, "username", source_chat_username)
            source_chat_title = getattr(sender_chat, "title", source_chat_title)
        if hasattr(fo, "message_id"):
            source_msg_id = fo.message_id

    source_link = make_source_link(source_chat_username, source_chat_id, source_msg_id)

    # ── archive to our private channel immediately ──
    archive_msg_id, archive_file_id = await _archive_message(msg.get_bot(), msg)

    # Prefer the archive file_id (lives in our channel) over the original
    stable_file_id = archive_file_id or file_id

    batch_no = dbase.next_batch_no()
    item_id = dbase.next_item_id()
    deep_link = build_deep_link(bot_username, item_id)

    doc = {
        "item_id": item_id,
        "batch_no": batch_no,
        "deep_link": deep_link,
        "media_kind": kind,
        # stable_file_id is from the archive channel – survives source deletion
        "file_id": stable_file_id,
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
        # archive metadata – lets you re-fetch via Telethon even if file_id expires
        "archive_channel_id": ARCHIVE_CHANNEL_ID,
        "archive_message_id": archive_msg_id,
        "added_by": uid,
        "created_at": datetime.utcnow(),
    }

    try:
        dbase.media_col.insert_one(doc)
        dbase.update_batch_count(batch_no, 1)
    except Exception as exc:
        return None, str(exc)

    return doc, None


async def save_range_by_links(
    begin_link: str, end_link: str, bot_username: str, uid: int
) -> dict:
    """
    Fetch a range of messages between two Telegram links via Telethon and save
    each media item to MongoDB.  Works for both public and private channels,
    and for messages of any age.
    """
    if not telethon_client:
        return {
            "saved": 0,
            "scanned": 0,
            "error": "Telethon is not available.  "
            "Set TG_API_ID / TG_API_HASH and run auth_telethon.py first.",
        }

    b = parse_link(begin_link)
    e = parse_link(end_link)
    if not b or not e:
        return {
            "saved": 0,
            "scanned": 0,
            "error": "Invalid begin or end link.",
        }

    if b["type"] != e["type"] or str(b["chat"]) != str(e["chat"]):
        return {
            "saved": 0,
            "scanned": 0,
            "error": "BEGIN and END links must be from the same chat.",
        }

    start_id = min(b["msg_id"], e["msg_id"])
    end_id = max(b["msg_id"], e["msg_id"])

    # Resolve entity: public channels by username, private by numeric ID
    entity = b["chat"] if b["type"] == "public" else int(f"-100{b['chat']}")

    scanned = saved = 0

    for mid in range(start_id, end_id + 1):
        scanned += 1
        try:
            m = await telethon_client.get_messages(entity, ids=mid)
            if not m or not m.media:
                continue

            if m.photo:
                kind = "photo"
            elif m.video:
                kind = "video"
            elif m.document:
                kind = "document"
            else:
                continue

            chat = await m.get_chat()
            source_chat_id = getattr(chat, "id", None)
            source_chat_username = getattr(chat, "username", None)
            source_chat_title = getattr(chat, "title", None)
            source_link = make_source_link(
                source_chat_username, source_chat_id, m.id
            )

            f = getattr(m, "file", None)
            batch_no = dbase.next_batch_no()
            item_id = dbase.next_item_id()

            doc = {
                "item_id": item_id,
                "batch_no": batch_no,
                "deep_link": build_deep_link(bot_username, item_id),
                "media_kind": kind,
                "file_id": None,  # Telethon IDs ≠ Bot API file_ids
                "file_unique_id": None,
                "file_name": getattr(f, "name", None),
                "mime_type": getattr(f, "mime_type", None),
                "file_size": getattr(f, "size", None),
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

            try:
                dbase.media_col.insert_one(doc)
                dbase.update_batch_count(batch_no, 1)
                saved += 1
            except Exception:
                # likely duplicate
                pass

        except Exception as exc:
            logger.warning("Error fetching msg %d: %s", mid, exc)

    return {"saved": saved, "scanned": scanned, "error": None}


# ────────────────────────────────────────────────────────────────
# Help text
# ────────────────────────────────────────────────────────────────

HELP_TEXT = """\
<b>📖 Bot Commands</b>

<b>General</b>
/start – Welcome message (or open a deep link)
/help  – Show this help

<b>Retrieval</b>
/get <code>&lt;item_id&gt;</code> – Fetch metadata &amp; deep link for one item
/send <code>&lt;batch_no&gt;</code> – Resend all media in a batch
/all   – Download a JSON file listing all batches &amp; items

<b>Admin only (sudo)</b>
/add       – Collect a range: provide BEGIN and END message links
/addoff    – Cancel an in-progress /add flow
/addmode on|off – Bulk streaming capture (forward media continuously)
/remove <code>&lt;item_id&gt;</code> – Delete an item

<b>Deep links</b>
Items are accessible via <code>t.me/YourBot?start=get_&lt;item_id&gt;</code>

<b>Batches</b>
Every 50 items form a new batch.  Use /send to replay a batch.
"""


# ────────────────────────────────────────────────────────────────
# Command handlers
# ────────────────────────────────────────────────────────────────


async def _send_file_from_doc(
    bot, chat_id: int, doc: dict, caption: str
) -> bool:
    """
    Try to send the file represented by `doc`.

    Priority
    --------
    1. Bot API file_id  (fast, no download)
    2. Telethon re-download from archive channel  (if file_id is stale)
    3. Return False so caller can fall back to a text reply.
    """
    fid = doc.get("file_id")
    kind = doc.get("media_kind")

    # ── attempt 1: Bot API file_id ──
    if fid:
        try:
            if kind == "photo":
                await bot.send_photo(
                    chat_id, fid, caption=caption, parse_mode=ParseMode.HTML
                )
                return True
            if kind == "video":
                await bot.send_video(
                    chat_id, fid, caption=caption, parse_mode=ParseMode.HTML
                )
                return True
            if kind in ("document", "audio", "voice", "animation"):
                await bot.send_document(
                    chat_id, fid, caption=caption, parse_mode=ParseMode.HTML
                )
                return True
        except Exception as exc:
            logger.warning(
                "file_id send failed (%s): %s – trying archive", fid, exc
            )

    # ── attempt 2: re-download from archive channel via Telethon ──
    arc_ch = doc.get("archive_channel_id")
    arc_mid = doc.get("archive_message_id")
    if telethon_client and arc_ch and arc_mid:
        try:
            import io as _io

            m = await telethon_client.get_messages(int(arc_ch), ids=int(arc_mid))
            if m and m.media:
                buf = _io.BytesIO()
                await telethon_client.download_media(m, file=buf)
                buf.seek(0)
                fname = doc.get("file_name") or "file"
                buf.name = fname
                await bot.send_document(
                    chat_id,
                    buf,
                    caption=caption,
                    parse_mode=ParseMode.HTML,
                )
                return True
        except Exception as exc:
            logger.warning("Telethon archive re-download failed: %s", exc)

    return False


async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """
    /start command.

    - Plain /start       → welcome text.
    - /start get_<id>   → fetch and display that item + deep link.
    """
    # Deep-link: ?start=get_<item_id>
    if context.args and context.args[0].startswith("get_"):
        item_id = context.args[0][4:]
        doc = dbase.media_col.find_one({"item_id": item_id})
        if not doc:
            await update.message.reply_text("❌ Item not found.")
            return

        text = (
            "📁 <b>Item found</b>\n\n"
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Batch: <b>{doc['batch_no']}</b>\n"
            f"Type: <b>{doc.get('media_kind', 'N/A')}</b>\n"
            f"File: {doc.get('file_name') or 'N/A'}\n"
            f"Size: {_fmt_size(doc.get('file_size'))}\n"
            f"Caption: {doc.get('caption') or '—'}\n"
            f"Source: {doc.get('source_link') or 'N/A'}\n"
            f"Added: {_fmt_dt(doc.get('created_at'))}\n\n"
            f"🔗 <a href=\"{doc['deep_link']}\">Deep link</a>"
        )

        sent = await _send_file_from_doc(
            context.bot, update.effective_chat.id, doc, text
        )
        if not sent:
            await update.message.reply_html(
                text, disable_web_page_preview=True
            )
        return

    # Default /start – simple welcome
    await update.message.reply_html(
        "👋 <b>Welcome!</b>\n\nUse /help to see available commands.",
        disable_web_page_preview=True,
    )


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_html(HELP_TEXT, disable_web_page_preview=True)


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /add is restricted to admins.")
        return
    await add_flow.start(uid, lambda t: update.message.reply_html(t))


async def addoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /addoff is restricted to admins.")
        return
    await add_flow.cancel(
        uid, lambda t: update.message.reply_text(t), "✅ Add flow cancelled."
    )


async def addmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /addmode is restricted to admins.")
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /addmode on  or  /addmode off")
        return

    if context.args[0].lower() == "on":
        await start_mode(
            uid,
            context.application,
            lambda t, **kw: update.message.reply_html(t, **kw),
        )
    else:
        await stop_mode(
            uid,
            lambda t, **kw: update.message.reply_html(t, **kw),
        )


async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text(
            "❌ /remove is restricted to admins."
        )
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <item_id>")
        return

    item_id = context.args[0].strip()
    doc = dbase.media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text(
            f"❌ Item <code>{item_id}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    dbase.media_col.delete_one({"item_id": item_id})
    dbase.update_batch_count(doc["batch_no"], -1)
    await update.message.reply_html(
        f"✅ Removed <code>{item_id}</code> from batch <b>{doc['batch_no']}</b>."
    )


async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <item_id>")
        return

    item_id = context.args[0].strip()
    doc = dbase.media_col.find_one({"item_id": item_id})
    if not doc:
        await update.message.reply_text(
            f"❌ Item <code>{item_id}</code> not found.",
            parse_mode=ParseMode.HTML,
        )
        return

    text = (
        "📁 <b>Item info</b>\n\n"
        f"ID: <code>{doc['item_id']}</code>\n"
        f"Batch: <b>{doc['batch_no']}</b>\n"
        f"Type: <b>{doc.get('media_kind', 'N/A')}</b>\n"
        f"File: {doc.get('file_name') or 'N/A'}\n"
        f"Size: {_fmt_size(doc.get('file_size'))}\n"
        f"MIME: {doc.get('mime_type') or 'N/A'}\n"
        f"Caption: {doc.get('caption') or '—'}\n"
        f"Source chat: {doc.get('source_chat_title') or doc.get('source_chat_username') or 'N/A'}\n"
        f"Source link: {doc.get('source_link') or 'N/A'}\n"
        f"Added: {_fmt_dt(doc.get('created_at'))}\n\n"
        f"🔗 <a href=\"{doc['deep_link']}\">Deep link</a>"
    )

    sent = await _send_file_from_doc(
        context.bot, update.effective_chat.id, doc, text
    )
    if not sent:
        await update.message.reply_html(
            text, disable_web_page_preview=True
        )


async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return

    batch_no = int(context.args[0])
    items = list(
        dbase.media_col.find({"batch_no": batch_no}).sort("created_at", 1)
    )

    if not items:
        await update.message.reply_text(
            f"No items found in batch {batch_no}."
        )
        return

    await update.message.reply_html(
        f"📦 Sending batch <b>{batch_no}</b> – {len(items)} item(s)…"
    )

    for doc in items:
        caption = (
            f"<code>{doc['item_id']}</code> | {doc.get('media_kind')} | "
            f"Batch {doc['batch_no']}\n"
            f"Source: {doc.get('source_link') or 'N/A'}\n"
            f"🔗 <a href=\"{doc['deep_link']}\">Deep link</a>"
        )
        sent = await _send_file_from_doc(
            context.bot, update.effective_chat.id, doc, caption
        )
        if not sent:
            await update.message.reply_html(
                caption, disable_web_page_preview=True
            )


async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batches = list(dbase.batch_col.find({}).sort("batch_no", 1))
    items = list(
        dbase.media_col.find(
            {}, {"_id": 0}
        ).sort([("batch_no", 1), ("created_at", 1)])
    )

    # normalise datetimes for JSON
    for it in items:
        if isinstance(it.get("created_at"), datetime):
            it["created_at"] = it["created_at"].isoformat() + "Z"

    live_counts: dict[int, int] = {}
    for it in items:
        live_counts[it["batch_no"]] = live_counts.get(it["batch_no"], 0) + 1

    batch_summary = [
        {"batch_no": b["batch_no"], "count": live_counts.get(b["batch_no"], 0)}
        for b in batches
    ]

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batch_summary),
        "total_items": len(items),
        "batch_summary": batch_summary,
        "items": items,
    }

    bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    bio.name = "all_batches.json"
    await update.message.reply_document(
        bio,
        caption=(
            "📊 <b>All batches export</b>\n"
            f"Batches: <b>{len(batch_summary)}</b> | "
            f"Items: <b>{len(items)}</b>"
        ),
        parse_mode=ParseMode.HTML,
    )


# ────────────────────────────────────────────────────────────────
# Message handlers
# ──────────────────────────────────────────────────────��─────────


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    uid = update.effective_user.id
    if not is_sudo(uid):
        # Non-admins: only respond if they came via a deep link (handled in start_cmd)
        return

    me = await context.bot.get_me()

    # ── AddMode takes priority ──
    if is_on(uid):
        await enqueue(uid, msg, me.username, save_one_media)
        return

    st = add_flow.get_state(uid)
    if not st.active:
        return  # no active flow; ignore

    # ── /add flow: BEGIN step accepts a forwarded message ──
    if st.step == "begin":
        st.begin_link = link_from_msg(msg)
        st.step = "end"
        st.retry_count = 0
        await msg.reply_html(
            "✅ <b>BEGIN</b> received from forwarded message.\n\n"
            "Step 2/2 – Now send the <b>END</b> link or forward the last message."
        )
        return

    # ── /add flow: END step accepts a forwarded message ──
    if st.step == "end":
        st.end_link = link_from_msg(msg)
        await msg.reply_html("✅ <b>END</b> received. Collecting range now…")
        await _run_range(msg, me.username, uid, st.begin_link, st.end_link)
        await add_flow.cancel(uid)
        return

    # ── Fallback: save a single uploaded / forwarded media ──
    doc, err = await save_one_media(msg, me.username, uid)
    if err:
        await msg.reply_text(f"❌ {err}")
        return
    await msg.reply_html(
        f"✅ Saved as <code>{doc['item_id']}</code> in batch <b>{doc['batch_no']}</b>.\n"
        f"🔗 <a href=\"{doc['deep_link']}\">Deep link</a>"
    )


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return

    uid = update.effective_user.id
    if not is_sudo(uid):
        return

    st = add_flow.get_state(uid)
    if not st.active:
        return

    res = await add_flow.handle_text(
        uid, msg.text.strip(), lambda t: msg.reply_html(t)
    )
    if res.get("ready"):
        me = await context.bot.get_me()
        await _run_range(
            msg, me.username, uid, res["begin_link"], res["end_link"]
        )
        await add_flow.cancel(uid)


# ────────────────────────────────────────────────────────────────
# Internal helpers
# ────────────────────────────────────────────────────────────────


async def _run_range(msg, bot_username, uid, begin_link, end_link):
    result = await save_range_by_links(
        begin_link, end_link, bot_username, uid
    )
    if result["error"]:
        await msg.reply_text(f"❌ {result['error']}")
    else:
        await msg.reply_html(
            "✅ <b>Range collection complete</b>\n\n"
            f"Scanned: <b>{result['scanned']}</b> messages\n"
            f"Saved: <b>{result['saved']}</b> media items\n"
            f"(Batches of {50} auto)"
        )


def _fmt_size(size) -> str:
    if not size:
        return "N/A"
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024:
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} TB"


def _fmt_dt(dt) -> str:
    if not dt:
        return "N/A"
    if isinstance(dt, datetime):
        return dt.strftime("%Y-%m-%d %H:%M UTC")
    return str(dt)

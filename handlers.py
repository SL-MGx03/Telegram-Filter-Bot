import io
import json
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from config import SUDO_ADMINS
from storage_service import (
    is_sudo,
    save_from_forward_or_upload,
    save_from_telegram_link,
    get_item,
    remove_item,
    get_batch_items,
    export_all,
)
from telethon_service import parse_tg_link

HELP_TEXT = """
<b>Commands</b>
/start - Start bot
/help - Help
/add - (sudo only) enter add mode, send telegram link or forward media
/get &lt;item_id&gt; - fetch one saved media/link
/send &lt;batch_no&gt; - send all items from batch
/all - export all batches/items as JSON file
/remove &lt;item_id&gt; - (sudo only) remove stored item
"""

async def start_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    # support deep link payload /start get_<id>
    if context.args and context.args[0].startswith("get_"):
        item_id = context.args[0][4:]
        doc = get_item(item_id)
        if not doc:
            await msg.reply_text("Item not found.")
            return
        text = (
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Batch: <b>{doc['batch_no']}</b>\n"
            f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
            f"Source: {doc.get('source_link') or 'N/A'}"
        )
        await msg.reply_text(text, parse_mode=ParseMode.HTML)
        return

    await msg.reply_text("Welcome.\nUse /help")

async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid, SUDO_ADMINS):
        await update.message.reply_text("❌ /add is sudo-only.")
        return
    context.user_data["add_mode"] = True
    await update.message.reply_text("✅ Add mode ON.\nNow send telegram message link OR forward/send media.")

async def remove_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid, SUDO_ADMINS):
        await update.message.reply_text("❌ /remove is sudo-only.")
        return
    if not context.args:
        await update.message.reply_text("Usage: /remove <item_id>")
        return

    ok, doc = remove_item(context.args[0].strip())
    if not ok:
        await update.message.reply_text("Item not found.")
        return
    await update.message.reply_text(f"✅ Removed {doc['item_id']} (batch {doc['batch_no']}).")

async def get_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /get <item_id>")
        return
    item_id = context.args[0].strip()
    doc = get_item(item_id)
    if not doc:
        await update.message.reply_text("Item not found.")
        return

    caption = (
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
            await update.message.reply_photo(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        elif kind == "video":
            await update.message.reply_video(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        elif kind == "document":
            await update.message.reply_document(doc["file_id"], caption=caption, parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text(caption, parse_mode=ParseMode.HTML)
    else:
        await update.message.reply_text(caption, parse_mode=ParseMode.HTML)

async def send_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Usage: /send <batch_no>")
        return
    batch_no = int(context.args[0])
    items = get_batch_items(batch_no)
    if not items:
        await update.message.reply_text("No items in this batch.")
        return

    await update.message.reply_text(f"Sending {len(items)} items from batch {batch_no} ...")
    for doc in items:
        text = (
            f"ID: <code>{doc['item_id']}</code>\n"
            f"Type: <b>{doc.get('media_kind','unknown')}</b>\n"
            f"File: <code>{doc.get('file_name') or 'N/A'}</code>\n"
            f"Source: {doc.get('source_link') or 'N/A'}"
        )
        if doc.get("file_id"):
            try:
                kind = doc.get("media_kind")
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
    payload = export_all()
    bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    bio.name = "all_batches.json"
    await update.message.reply_document(document=bio, caption="All batches/items export.")

async def media_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return
    uid = update.effective_user.id

    if not context.user_data.get("add_mode", False):
        return
    if not is_sudo(uid, SUDO_ADMINS):
        await msg.reply_text("❌ Not allowed.")
        return

    me = await context.bot.get_me()
    doc, err = await save_from_forward_or_upload(msg, me.username, uid)
    if err:
        await msg.reply_text(f"❌ {err}")
        return
    await msg.reply_text(
        f"✅ Saved.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
        parse_mode=ParseMode.HTML
    )

async def text_msg_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    uid = update.effective_user.id

    if not context.user_data.get("add_mode", False):
        return
    if not is_sudo(uid, SUDO_ADMINS):
        await msg.reply_text("❌ Not allowed.")
        return

    text = msg.text.strip()
    if not parse_tg_link(text):
        await msg.reply_text("Send a valid telegram message link:\nhttps://t.me/<user>/<id>\nor https://t.me/c/<id>/<msg_id>")
        return

    me = await context.bot.get_me()
    doc, err = await save_from_telegram_link(text, me.username, uid)
    if err:
        await msg.reply_text(f"❌ {err}")
        return

    await msg.reply_text(
        f"✅ Saved from link.\nID: <code>{doc['item_id']}</code>\nBatch: <b>{doc['batch_no']}</b>\nDeep: {doc['deep_link']}",
        parse_mode=ParseMode.HTML
    )

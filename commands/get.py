import database as dbase
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


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
        f"Type: <b>{doc.get('media_kind')}</b>\n"
        f"Source: {doc.get('source_link') or 'N/A'}"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.HTML)

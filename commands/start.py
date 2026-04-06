import database as dbase
from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes


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

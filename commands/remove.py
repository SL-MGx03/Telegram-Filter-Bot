import database as dbase
from telegram import Update
from telegram.ext import ContextTypes

from utils.helpers import is_sudo


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

import database as dbase
from telegram import Update
from telegram.ext import ContextTypes


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

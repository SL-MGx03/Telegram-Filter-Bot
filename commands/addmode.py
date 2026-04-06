from telegram import Update
from telegram.ext import ContextTypes

from addmode import start_mode, stop_mode
from utils.helpers import is_sudo


async def addmode_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /addmode sudo-only.")
        return
    if not context.args or context.args[0].lower() not in ("on", "off"):
        await update.message.reply_text("Usage: /addmode on|off")
        return

    mode = context.args[0].lower()
    if mode == "on":
        await start_mode(uid, context.application, update.message.reply_text)
    else:
        await stop_mode(uid, update.message.reply_text)

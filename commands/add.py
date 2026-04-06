from telegram import Update
from telegram.ext import ContextTypes

from add_flow import add_flow
from utils.helpers import is_sudo


async def add_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /add sudo-only.")
        return
    await add_flow.start(uid, update.message.reply_text)

from telegram import Update
from telegram.ext import ContextTypes

from add_flow import add_flow
from utils.helpers import is_sudo


async def addoff_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_sudo(uid):
        await update.message.reply_text("❌ /addoff sudo-only.")
        return
    await add_flow.cancel(uid, update.message.reply_text, "✅ Add cancelled.")

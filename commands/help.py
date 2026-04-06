from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

HELP_TEXT = """
<b>Commands</b>
/start
/help
/add (sudo): asks BEGIN then END (link or forward)
/addoff (sudo): cancel add mode
/get &lt;item_id&gt;
/send &lt;batch_no&gt;
/all
/remove &lt;item_id&gt; (sudo)
/addmode on|off (sudo): bulk auto-capture media
"""


async def help_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_TEXT, parse_mode=ParseMode.HTML)

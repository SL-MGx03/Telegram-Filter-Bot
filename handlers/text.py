from telegram import Update
from telegram.ext import ContextTypes

from add_flow import add_flow
from services.range_store import save_range_by_links
from utils.helpers import is_sudo


async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg or not msg.text:
        return
    uid = update.effective_user.id
    text = msg.text.strip()

    st = add_flow.get_state(uid)
    if st.active and is_sudo(uid):
        res = await add_flow.handle_text(uid, text, msg.reply_text)
        if res.get("ready"):
            me = await context.bot.get_me()
            result = await save_range_by_links(res["begin_link"], res["end_link"], me.username, uid)
            if result["error"]:
                await msg.reply_text(f"❌ {result['error']}")
                await add_flow.cancel(uid)
                return
            await msg.reply_text(
                f"✅ Completed.\nScanned: {result['scanned']}\nSaved media: {result['saved']}\n(Batch size: 50 auto)"
            )
            await add_flow.cancel(uid)
        return

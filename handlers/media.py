from telegram import Update
from telegram.ext import ContextTypes

from add_flow import add_flow
from addmode import is_on, enqueue
from services.media_store import save_one_media
from services.range_store import save_range_by_links
from utils.helpers import is_sudo, link_from_msg


async def media_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    msg = update.message
    if not msg:
        return

    uid = update.effective_user.id
    if not is_sudo(uid):
        return

    me = await context.bot.get_me()

    # AddMode priority
    if is_on(uid):
        await enqueue(uid, msg, me.username, save_one_media)
        return

    st = add_flow.get_state(uid)
    if not st.active:
        return

    # BEGIN can be forwarded/media
    if st.step == "begin":
        st.begin_link = link_from_msg(msg)
        st.step = "end"
        st.retry_count = 0
        await msg.reply_text("✅ Begin received (from media/forward).\nNow send END link OR forward END message/media.")
        return

    # END can be forwarded/media
    if st.step == "end":
        st.end_link = link_from_msg(msg)
        await msg.reply_text("✅ End received. Processing range now...")
        result = await save_range_by_links(st.begin_link, st.end_link, me.username, uid)
        if result["error"]:
            await msg.reply_text(f"❌ {result['error']}")
            await add_flow.cancel(uid)
            return
        await msg.reply_text(
            f"✅ Completed.\nScanned: {result['scanned']}\nSaved media: {result['saved']}\n(Batch size: 50 auto)"
        )
        await add_flow.cancel(uid)
        return

    # fallback single save
    doc, err = await save_one_media(msg, me.username, uid)
    if err:
        await msg.reply_text(f"❌ {err}")
        return
    await msg.reply_text(f"✅ Saved {doc['item_id']} in batch {doc['batch_no']}.")

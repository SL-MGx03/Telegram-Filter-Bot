import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from config import BOT_TOKEN
import database as dbase
from bot_handlers import (
    init_telethon,
    start_cmd, help_cmd, add_cmd, addoff_cmd, remove_cmd, get_cmd, send_cmd, all_cmd,
    media_handler, text_handler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(app):
    try:
        dbase.init_db()
        logger.info("MongoDB connected.")
        await init_telethon()
        me = await app.bot.get_me()
        logger.info("Bot started as @%s", me.username)
    except Exception as e:
        logger.exception("Startup failed: %s", e)
        raise

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("addoff", addoff_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(CommandHandler("addmode", addmode_cmd))

    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.ANIMATION,
        media_handler
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

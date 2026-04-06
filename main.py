import logging
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
)

from config import BOT_TOKEN
from db import ensure_indexes
from telethon_service import init_telethon
from handlers import (
    start_cmd, help_cmd, add_cmd, remove_cmd, get_cmd, send_cmd, all_cmd,
    media_msg_handler, text_msg_handler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)

async def post_init(app):
    ensure_indexes()
    await init_telethon()

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("all", all_cmd))

    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | filters.AUDIO | filters.VOICE | filters.ANIMATION,
        media_msg_handler
    ))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_msg_handler))

    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

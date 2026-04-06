"""
main.py  –  Entry point for the Telegram deep-link bot.
Run locally:  python main.py
Run on Railway:  set env vars and deploy; Procfile calls this.
"""

import logging
from telegram.ext import (
    ApplicationBuilder, CommandHandler, MessageHandler, filters
)

from config import BOT_TOKEN
import database as dbase
from bot_handlers import (
    init_telethon,
    start_cmd, help_cmd,
    add_cmd, addoff_cmd, addmode_cmd,
    remove_cmd, get_cmd, send_cmd, all_cmd,
    media_handler, text_handler,
)

logging.basicConfig(
    format="%(asctime)s | %(name)s | %(levelname)s | %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


async def post_init(app):
    """Called once after the Application is built, before polling starts."""
    try:
        dbase.init_db()
        logger.info("MongoDB connected.")
    except Exception as exc:
        logger.exception("MongoDB init failed: %s", exc)
        raise

    await init_telethon()

    me = await app.bot.get_me()
    logger.info("Bot started as @%s (id=%d)", me.username, me.id)


def main():
    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(post_init)
        .build()
    )

    # ── command handlers ──
    app.add_handler(CommandHandler("start",   start_cmd))
    app.add_handler(CommandHandler("help",    help_cmd))
    app.add_handler(CommandHandler("add",     add_cmd))
    app.add_handler(CommandHandler("addoff",  addoff_cmd))
    app.add_handler(CommandHandler("addmode", addmode_cmd))
    app.add_handler(CommandHandler("remove",  remove_cmd))
    app.add_handler(CommandHandler("get",     get_cmd))
    app.add_handler(CommandHandler("send",    send_cmd))
    app.add_handler(CommandHandler("all",     all_cmd))

    # ── media / text message handlers ──
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL |
        filters.AUDIO | filters.VOICE | filters.ANIMATION,
        media_handler,
    ))
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        text_handler,
    ))

    logger.info("Starting polling…")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()

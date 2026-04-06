"""
Main Bot Entry Point
IMPROVED: Better logging and error handling
"""

import logging
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters

from config import BOT_TOKEN
import database as dbase
from bot_handlers import (
    init_telethon,
    start_cmd, help_cmd, add_cmd, addoff_cmd, remove_cmd, get_cmd, get_batch_cmd,
    send_cmd, all_cmd, addmode_cmd,
    media_handler, text_handler
)

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger(__name__)

async def post_init(app):
    """Initialize bot dependencies"""
    try:
        dbase.init_db()
        logger.info("✅ MongoDB connected")
        
        await init_telethon()
        logger.info("✅ Telethon initialized")
        
        me = await app.bot.get_me()
        logger.info(f"✅ Bot started: @{me.username}")
        
        # Log stats
        stats = dbase.backup_statistics()
        logger.info(f"   Items: {stats['total_items']}, Batches: {stats['total_batches']}")
        
    except Exception as e:
        logger.exception(f"❌ Startup failed: {e}")
        raise

def main():
    """Start bot"""
    app = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init).build()

    # Command handlers
    app.add_handler(CommandHandler("start", start_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("add", add_cmd))
    app.add_handler(CommandHandler("addoff", addoff_cmd))
    app.add_handler(CommandHandler("remove", remove_cmd))
    app.add_handler(CommandHandler("get", get_cmd))
    app.add_handler(CommandHandler("get_batch", get_batch_cmd))
    app.add_handler(CommandHandler("send", send_cmd))
    app.add_handler(CommandHandler("all", all_cmd))
    app.add_handler(CommandHandler("addmode", addmode_cmd))

    # Media handler
    app.add_handler(MessageHandler(
        filters.PHOTO | filters.VIDEO | filters.Document.ALL | 
        filters.AUDIO | filters.VOICE | filters.ANIMATION,
        media_handler
    ))
    
    # Text handler
    app.add_handler(MessageHandler(
        filters.TEXT & ~filters.COMMAND,
        text_handler
    ))

    logger.info("🚀 Bot polling started...")
    app.run_polling(drop_pending_updates=True)

if __name__ == "__main__":
    main()

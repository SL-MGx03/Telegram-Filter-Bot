import logging

from telethon import TelegramClient

from config import TG_API_ID, TG_API_HASH, TG_SESSION_NAME

logger = logging.getLogger(__name__)
telethon_client = None


async def init_telethon():
    global telethon_client
    if not (TG_API_ID and TG_API_HASH and TG_SESSION_NAME):
        telethon_client = None
        logger.info("Telethon disabled.")
        return
    try:
        telethon_client = TelegramClient(TG_SESSION_NAME, int(TG_API_ID), TG_API_HASH)
        await telethon_client.connect()
        if not await telethon_client.is_user_authorized():
            await telethon_client.disconnect()
            telethon_client = None
            logger.warning("Telethon session not authorized.")
            return
        logger.info("Telethon ready.")
    except Exception as e:
        telethon_client = None
        logger.warning(f"Telethon init failed: {e}")

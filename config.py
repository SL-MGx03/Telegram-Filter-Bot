import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "telegram_deeplink_bot").strip()

SUDO_ADMINS = set()
for x in os.getenv("SUDO_ADMINS", "").split(","):
    x = x.strip()
    if x.isdigit():
        SUDO_ADMINS.add(int(x))

# Optional: user account for old/private link access (Telethon)
TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_NAME = os.getenv("TG_SESSION_NAME", "railway_user_session").strip()

BATCH_SIZE = 50

if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is required.")

import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
MONGODB_URI = os.getenv("MONGODB_URI", "").strip()
MONGODB_DB = os.getenv("MONGODB_DB", "tg_deeplink_bot").strip()

SUDO_ADMINS: set[int] = set()
for _x in os.getenv("SUDO_ADMINS", "").split(","):
    _x = _x.strip()
    if _x.isdigit():
        SUDO_ADMINS.add(int(_x))

# Telethon user-account (needed to fetch old / private channel messages)
TG_API_ID = os.getenv("TG_API_ID", "").strip()
TG_API_HASH = os.getenv("TG_API_HASH", "").strip()
TG_SESSION_NAME = os.getenv("TG_SESSION_NAME", "railway_session").strip()

# How many items go into one batch
BATCH_SIZE = 50

# Private channel where every file is re-uploaded for permanent storage.
# Create a private channel, add your bot as admin with "Post messages" rights,
# then paste its numeric ID here (e.g. -1001234567890).
# Leave blank to disable archive (files will only be referenced by file_id).
_arc = os.getenv("ARCHIVE_CHANNEL_ID", "").strip()
ARCHIVE_CHANNEL_ID: int | None = int(_arc) if _arc.lstrip("-").isdigit() else None

# ----- sanity checks -----
if not BOT_TOKEN:
    raise RuntimeError("BOT_TOKEN is required.")
if not MONGODB_URI:
    raise RuntimeError("MONGODB_URI is required.")

"""
auth_telethon.py
~~~~~~~~~~~~~~~~
Run this ONCE on your local machine to create the Telethon session file, then
upload the session string to Railway as the TG_SESSION_NAME env var (or use
StringSession – see comments below).

Usage
-----
    python auth_telethon.py

You will be prompted for your phone number and the OTP Telegram sends you.
The session file will be saved as <TG_SESSION_NAME>.session in the current
directory.

For Railway (no persistent disk)
---------------------------------
Switch to StringSession so the session is stored as an environment variable:

    1. Uncomment the StringSession block below.
    2. Copy the printed string into Railway → Variables → TG_STRING_SESSION.
    3. In bot_handlers.py replace:
           TelegramClient(TG_SESSION_NAME, ...)
       with:
           from telethon.sessions import StringSession
           TelegramClient(StringSession(os.getenv("TG_STRING_SESSION")), ...)
"""

import asyncio
import os
from dotenv import load_dotenv
from telethon import TelegramClient
# from telethon.sessions import StringSession   # ← uncomment for Railway

load_dotenv()

API_ID   = int(os.getenv("TG_API_ID", "0"))
API_HASH = os.getenv("TG_API_HASH", "")
SESSION  = os.getenv("TG_SESSION_NAME", "railway_session")


async def main():
    # client = TelegramClient(StringSession(), API_ID, API_HASH)  # ← Railway
    client = TelegramClient(SESSION, API_ID, API_HASH)

    await client.start()          # prompts phone + OTP interactively
    me = await client.get_me()
    print(f"\n✅  Authorised as {me.first_name} (@{me.username})  id={me.id}")

    # ── StringSession output (Railway) ──
    # print("\n🔑  String session (save to TG_STRING_SESSION):")
    # print(client.session.save())

    await client.disconnect()
    print(f"\nSession saved to:  {SESSION}.session")
    print("You can now deploy the bot.")


if __name__ == "__main__":
    asyncio.run(main())

import asyncio
from datetime import datetime
from telegram.constants import ParseMode

import database as dbase

AUTO_DELETE_CAPTURED = True
PROGRESS_EVERY = 50

_state = {}
_lock = asyncio.Lock()

def _user_state(uid: int):
    if uid not in _state:
        _state[uid] = {
            "on": False,
            "started_at": None,
            "saved_this_run": 0,
            "saved_total": 0,
            "last_id": None,
            "queue": asyncio.Queue(),
            "worker": None,
        }
    return _state[uid]

async def start_mode(uid: int, app, reply):
    async with _lock:
        st = _user_state(uid)
        if st["on"]:
            await reply("⚠️ /addmode already ON.")
            return
        st["on"] = True
        st["started_at"] = datetime.utcnow()
        st["saved_this_run"] = 0
        if st["worker"] and not st["worker"].done():
            st["worker"].cancel()
        st["worker"] = asyncio.create_task(_worker(uid, app))
        await reply("✅ AddMode ON. Send/forward media now. Use /addmode off to stop.")

async def stop_mode(uid: int, reply):
    async with _lock:
        st = _user_state(uid)
        if not st["on"]:
            await reply("⚠️ /addmode already OFF.")
            return
        st["on"] = False
        if st["worker"] and not st["worker"].done():
            st["worker"].cancel()
        await reply(
            f"✅ AddMode OFF.\nSaved this run: <b>{st['saved_this_run']}</b>\n"
            f"Total saved: <b>{st['saved_total']}</b>\n"
            f"Last ID: <code>{st['last_id'] or 'N/A'}</code>",
            parse_mode=ParseMode.HTML
        )

def is_on(uid: int) -> bool:
    return _user_state(uid)["on"]

async def enqueue(uid: int, msg, bot_username: str, save_fn):
    st = _user_state(uid)
    if not st["on"]:
        return
    await st["queue"].put((msg, bot_username, save_fn))

async def _worker(uid: int, app):
    st = _user_state(uid)
    while st["on"]:
        try:
            msg, bot_username, save_fn = await st["queue"].get()
            try:
                doc, err = await save_fn(msg, bot_username, uid)
                if not err and doc:
                    st["saved_this_run"] += 1
                    st["saved_total"] += 1
                    st["last_id"] = doc["item_id"]

                    if st["saved_this_run"] % PROGRESS_EVERY == 0:
                        await app.bot.send_message(
                            chat_id=msg.chat_id,
                            text=(
                                f"✅ AddMode progress\n"
                                f"Saved this run: {st['saved_this_run']}\n"
                                f"Total saved: {st['saved_total']}\n"
                                f"Last ID: <code>{st['last_id']}</code>"
                            ),
                            parse_mode=ParseMode.HTML
                        )

                    if AUTO_DELETE_CAPTURED:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            finally:
                st["queue"].task_done()
        except asyncio.CancelledError:
            break
        except Exception:
            await asyncio.sleep(0.2)

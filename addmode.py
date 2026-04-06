"""
AddMode - Bulk media capture
IMPROVED: Better queue management and atomic operations
"""

import asyncio
from datetime import datetime
from telegram.constants import ParseMode
import logging

import database as dbase

logger = logging.getLogger(__name__)

AUTO_DELETE_CAPTURED = True
PROGRESS_EVERY = 50

_state = {}
_lock = asyncio.Lock()

def _user_state(uid: int) -> dict:
    """Get or create user state"""
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
    """Start bulk capture mode"""
    async with _lock:
        st = _user_state(uid)
        if st["on"]:
            await reply("⚠️ AddMode already ON")
            return
        
        st["on"] = True
        st["started_at"] = datetime.utcnow()
        st["saved_this_run"] = 0
        
        if st["worker"] and not st["worker"].done():
            st["worker"].cancel()
        
        st["worker"] = asyncio.create_task(_worker(uid, app))
        await reply("✅ AddMode ON\nSend/forward media. Use /addmode off to stop")

async def stop_mode(uid: int, reply):
    """Stop bulk capture mode"""
    async with _lock:
        st = _user_state(uid)
        if not st["on"]:
            await reply("⚠️ AddMode already OFF")
            return
        
        st["on"] = False
        if st["worker"] and not st["worker"].done():
            st["worker"].cancel()
        
        elapsed = ""
        if st["started_at"]:
            elapsed = f" ({(datetime.utcnow() - st['started_at']).total_seconds():.0f}s)"
        
        await reply(
            f"✅ AddMode OFF{elapsed}\n"
            f"Saved this run: <b>{st['saved_this_run']}</b>\n"
            f"Total: <b>{st['saved_total']}</b>\n"
            f"Last: <code>{st['last_id'] or 'N/A'}</code>",
            parse_mode=ParseMode.HTML
        )

def is_on(uid: int) -> bool:
    """Check if user is in add mode"""
    return _user_state(uid)["on"]

async def enqueue(uid: int, msg, bot_username: str, save_fn):
    """Add message to queue"""
    st = _user_state(uid)
    if not st["on"]:
        return
    await st["queue"].put((msg, bot_username, save_fn))

async def _worker(uid: int, app):
    """Process queued messages"""
    st = _user_state(uid)
    
    while st["on"]:
        try:
            # Get next message from queue
            msg, bot_username, save_fn = await asyncio.wait_for(
                st["queue"].get(),
                timeout=1.0
            )
            
            try:
                # ATOMIC: Save media
                doc, err = await save_fn(msg, bot_username, uid)
                
                if not err and doc:
                    st["saved_this_run"] += 1
                    st["saved_total"] += 1
                    st["last_id"] = doc["item_id"]
                    
                    # Progress report
                    if st["saved_this_run"] % PROGRESS_EVERY == 0:
                        try:
                            await app.bot.send_message(
                                chat_id=msg.chat_id,
                                text=(
                                    f"✅ Progress: {st['saved_this_run']}\n"
                                    f"Last: <code>{st['last_id']}</code>"
                                ),
                                parse_mode=ParseMode.HTML
                            )
                        except Exception as e:
                            logger.warning(f"Progress message failed: {e}")
                    
                    # Auto-delete
                    if AUTO_DELETE_CAPTURED:
                        try:
                            await msg.delete()
                        except Exception:
                            pass
            
            finally:
                st["queue"].task_done()
        
        except asyncio.TimeoutError:
            await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            logger.info(f"AddMode worker {uid} cancelled")
            break
        except Exception as e:
            logger.exception(f"Worker error: {e}")
            await asyncio.sleep(0.2)

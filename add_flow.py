import asyncio
import time
import re
from dataclasses import dataclass
from typing import Optional

ADD_TIMEOUT_SEC = 120
MAX_RETRY = 2

def parse_link(text: str):
    if not text:
        return None
    text = text.strip()
    m1 = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)$", text)
    if m1:
        return {"type": "public", "chat": m1.group(1), "msg_id": int(m1.group(2)), "raw": text}
    m2 = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", text)
    if m2:
        return {"type": "private", "chat": int(m2.group(1)), "msg_id": int(m2.group(2)), "raw": text}
    return None

@dataclass
class AddState:
    active: bool = False
    step: str = "idle"   # begin|end|collect
    begin_link: Optional[str] = None
    end_link: Optional[str] = None
    retry_count: int = 0
    created_at: float = 0.0
    timeout_task: Optional[asyncio.Task] = None

class AddFlowManager:
    def __init__(self):
        self.user_states: dict[int, AddState] = {}
        self.user_locks: dict[int, asyncio.Lock] = {}
        self.rate: dict[int, list[float]] = {}  # anti spam timestamps

    def _lock(self, uid: int):
        if uid not in self.user_locks:
            self.user_locks[uid] = asyncio.Lock()
        return self.user_locks[uid]

    def get_state(self, uid: int) -> AddState:
        if uid not in self.user_states:
            self.user_states[uid] = AddState()
        return self.user_states[uid]

    def spam_blocked(self, uid: int, limit=7, window=10):
        now = time.time()
        arr = self.rate.get(uid, [])
        arr = [x for x in arr if now - x <= window]
        arr.append(now)
        self.rate[uid] = arr
        return len(arr) > limit

    async def start(self, uid: int, send_fn):
        async with self._lock(uid):
            st = self.get_state(uid)
            if st.active:
                await send_fn("⚠️ /add already in progress. Finish it or wait timeout.")
                return False

            st.active = True
            st.step = "begin"
            st.begin_link = None
            st.end_link = None
            st.retry_count = 0
            st.created_at = time.time()

            if st.timeout_task and not st.timeout_task.done():
                st.timeout_task.cancel()

            st.timeout_task = asyncio.create_task(self._timeout(uid, send_fn))
            await send_fn("✅ Add started.\nSend BEGIN telegram message link or forward BEGIN media.")
            return True

    async def cancel(self, uid: int, send_fn=None, reason="Cancelled."):
        async with self._lock(uid):
            st = self.get_state(uid)
            if st.timeout_task and not st.timeout_task.done():
                st.timeout_task.cancel()
            self.user_states[uid] = AddState()
            if send_fn:
                await send_fn(reason)

    async def _timeout(self, uid: int, send_fn):
        await asyncio.sleep(ADD_TIMEOUT_SEC)
        st = self.get_state(uid)
        if st.active:
            await self.cancel(uid, send_fn, "⌛ Add operation timed out (2 min). Cancelled.")

    async def handle_text(self, uid: int, text: str, send_fn):
        async with self._lock(uid):
            st = self.get_state(uid)
            if not st.active:
                return {"handled": False}

            if self.spam_blocked(uid):
                await send_fn("⛔ Slow down. Too many messages.")
                return {"handled": True, "blocked": True}

            parsed = parse_link(text)
            if st.step in ("begin", "end"):
                if not parsed:
                    st.retry_count += 1
                    if st.retry_count >= MAX_RETRY:
                        await self.cancel(uid, send_fn, "❌ Invalid link again. Add cancelled.")
                        return {"handled": True, "cancelled": True}
                    await send_fn(f"❌ Invalid link. Send valid t.me link again. ({st.retry_count}/{MAX_RETRY})")
                    return {"handled": True, "need_retry": True}

                if st.step == "begin":
                    st.begin_link = parsed["raw"]
                    st.step = "end"
                    st.retry_count = 0
                    await send_fn("✅ Begin received.\nNow send END telegram message link.")
                    return {"handled": True, "step": "end"}

                if st.step == "end":
                    st.end_link = parsed["raw"]
                    st.step = "collect"
                    await send_fn("✅ End received. Processing range now...")
                    return {
                        "handled": True,
                        "ready": True,
                        "begin_link": st.begin_link,
                        "end_link": st.end_link
                    }

            return {"handled": False}


# Shared singleton used by commands and handlers
add_flow = AddFlowManager()

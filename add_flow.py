"""
add_flow.py
~~~~~~~~~~~
State-machine for the multi-step /add command.

Steps
-----
1. Admin sends /add  → bot asks for BEGIN link
2. Admin sends BEGIN telegram link (or forwards the BEGIN message)
3. Bot asks for END link
4. Admin sends END telegram link (or forwards the END message)
5. Bot triggers range-collection and resets state

A 2-minute timeout cancels the flow automatically.
"""

import asyncio
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Awaitable, Optional

ADD_TIMEOUT_SEC = 120
MAX_RETRY = 2


# ── link parser ──────────────────────────────────────────────────────────────

def parse_link(text: str) -> Optional[dict]:
    """
    Returns
    -------
    {"type": "public"|"private", "chat": str|int, "msg_id": int, "raw": str}
    or None if the text is not a recognised Telegram message link.
    """
    if not text:
        return None
    text = text.strip()

    # Public: https://t.me/username/123
    m = re.match(r"^https?://t\.me/([A-Za-z0-9_]+)/(\d+)$", text)
    if m:
        return {"type": "public", "chat": m.group(1),
                "msg_id": int(m.group(2)), "raw": text}

    # Private: https://t.me/c/1234567890/123
    m = re.match(r"^https?://t\.me/c/(\d+)/(\d+)$", text)
    if m:
        return {"type": "private", "chat": int(m.group(1)),
                "msg_id": int(m.group(2)), "raw": text}

    return None


# ── state dataclass ───────────────────────────────────────────────────────────

@dataclass
class AddState:
    active: bool = False
    step: str = "idle"           # idle | begin | end | collect
    begin_link: Optional[str] = None
    end_link: Optional[str] = None
    retry_count: int = 0
    created_at: float = 0.0
    timeout_task: Optional[asyncio.Task] = field(default=None, repr=False)


# ── manager ───────────────────────────────────────────────────────────────────

SendFn = Callable[[str], Awaitable[None]]


class AddFlowManager:
    def __init__(self):
        self._states: dict[int, AddState] = {}
        self._locks: dict[int, asyncio.Lock] = {}
        self._rate: dict[int, list[float]] = {}

    # -- internal helpers --

    def _lock(self, uid: int) -> asyncio.Lock:
        if uid not in self._locks:
            self._locks[uid] = asyncio.Lock()
        return self._locks[uid]

    def get_state(self, uid: int) -> AddState:
        if uid not in self._states:
            self._states[uid] = AddState()
        return self._states[uid]

    def _spam_blocked(self, uid: int, limit=7, window=10) -> bool:
        now = time.time()
        arr = [t for t in self._rate.get(uid, []) if now - t <= window]
        arr.append(now)
        self._rate[uid] = arr
        return len(arr) > limit

    # -- public API --

    async def start(self, uid: int, send_fn: SendFn) -> bool:
        async with self._lock(uid):
            st = self.get_state(uid)
            if st.active:
                await send_fn("⚠️ An /add is already in progress. "
                              "Finish it, send /addoff to cancel, or wait for timeout.")
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

            await send_fn(
                "✅ <b>Add flow started.</b>\n\n"
                "Step 1/2 – Send the <b>BEGIN</b> Telegram message link "
                "(e.g. <code>https://t.me/channel/1</code>) "
                "or simply <b>forward</b> the first message.",
            )
            return True

    async def cancel(self, uid: int, send_fn: Optional[SendFn] = None,
                     reason: str = "❌ Add cancelled."):
        async with self._lock(uid):
            st = self.get_state(uid)
            if st.timeout_task and not st.timeout_task.done():
                st.timeout_task.cancel()
            self._states[uid] = AddState()
            if send_fn:
                await send_fn(reason)

    async def _timeout(self, uid: int, send_fn: SendFn):
        await asyncio.sleep(ADD_TIMEOUT_SEC)
        st = self.get_state(uid)
        if st.active:
            await self.cancel(uid, send_fn,
                              "⌛ Add timed out (2 min). Flow cancelled.")

    async def handle_text(self, uid: int, text: str, send_fn: SendFn) -> dict:
        """
        Returns a dict with keys:
          handled, blocked, cancelled, need_retry, step, ready,
          begin_link, end_link
        """
        async with self._lock(uid):
            st = self.get_state(uid)
            if not st.active:
                return {"handled": False}

            if self._spam_blocked(uid):
                await send_fn("⛔ Slow down – too many messages.")
                return {"handled": True, "blocked": True}

            parsed = parse_link(text)

            if st.step in ("begin", "end"):
                if not parsed:
                    st.retry_count += 1
                    if st.retry_count >= MAX_RETRY:
                        await self.cancel(uid, send_fn,
                                          "❌ Invalid link twice. Add cancelled.")
                        return {"handled": True, "cancelled": True}
                    await send_fn(
                        f"❌ Invalid link. Please send a valid <code>t.me</code> link. "
                        f"({st.retry_count}/{MAX_RETRY})"
                    )
                    return {"handled": True, "need_retry": True}

                if st.step == "begin":
                    st.begin_link = parsed["raw"]
                    st.step = "end"
                    st.retry_count = 0
                    await send_fn(
                        "✅ <b>BEGIN</b> link saved.\n\n"
                        "Step 2/2 – Now send the <b>END</b> Telegram message link "
                        "or forward the last message in the range."
                    )
                    return {"handled": True, "step": "end"}

                # step == "end"
                st.end_link = parsed["raw"]
                st.step = "collect"
                await send_fn("✅ <b>END</b> link saved. Collecting range now…")
                return {
                    "handled": True,
                    "ready": True,
                    "begin_link": st.begin_link,
                    "end_link": st.end_link,
                }

            return {"handled": False}

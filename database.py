"""
MongoDB helpers.

Collections
-----------
media_items  – one doc per saved media file / deep-link
batches      – one doc per batch (tracks item count)
counters     – auto-increment sequences
"""

from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING
import certifi

from config import MONGODB_URI, MONGODB_DB, BATCH_SIZE

mongo_client = None
db = None
media_col = None
batch_col = None


# ── initialisation ──────────────────────────────────────────────────────────

def init_db():
    global mongo_client, db, media_col, batch_col
    mongo_client = MongoClient(
        MONGODB_URI,
        tls=True,
        tlsCAFile=certifi.where(),
        serverSelectionTimeoutMS=30_000,
    )
    mongo_client.admin.command("ping")

    db = mongo_client[MONGODB_DB]
    media_col = db["media_items"]
    batch_col = db["batches"]

    # indexes
    media_col.create_index([("item_id", ASCENDING)], unique=True)
    media_col.create_index([("batch_no", ASCENDING)])
    media_col.create_index([("created_at", DESCENDING)])
    media_col.create_index([("file_unique_id", ASCENDING)])
    batch_col.create_index([("batch_no", ASCENDING)], unique=True)


# ── batch helpers ────────────────────────────────────────────────────────────

def next_batch_no() -> int:
    """Return the current open batch number (create new one if current is full)."""
    last = batch_col.find_one(sort=[("batch_no", DESCENDING)])
    if not last:
        batch_col.insert_one({
            "batch_no": 1, "count": 0,
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
        })
        return 1

    if last.get("count", 0) < BATCH_SIZE:
        return last["batch_no"]

    new_no = last["batch_no"] + 1
    batch_col.insert_one({
        "batch_no": new_no, "count": 0,
        "created_at": datetime.utcnow(),
        "updated_at": datetime.utcnow(),
    })
    return new_no


def update_batch_count(batch_no: int, delta: int):
    batch_col.update_one(
        {"batch_no": batch_no},
        {"$inc": {"count": delta}, "$set": {"updated_at": datetime.utcnow()}},
        upsert=True,
    )


# ── counter helpers ───────────────────────────────────────────────────────────

def next_item_id() -> str:
    """Generate a short sequential ID like a1, a2, … a999, …"""
    doc = db["counters"].find_one_and_update(
        {"_id": "item_counter"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=True,
    )
    return f"a{doc.get('seq', 1)}"

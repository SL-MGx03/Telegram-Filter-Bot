"""
Enhanced Database Module - Fixed Atomic Operations
- Fixes: Race condition in batch numbering (now saves all 50+ messages)
- Fixes: Atomic counter increment for item_id
- New: Permanent file reference storage
- New: Batch metadata tracking
"""

from datetime import datetime
from pymongo import MongoClient, ASCENDING, DESCENDING, ReturnDocument
import certifi
import logging

from config import MONGODB_URI, MONGODB_DB, BATCH_SIZE

logger = logging.getLogger(__name__)

mongo_client = None
db = None
media_col = None
batch_col = None
counter_col = None

def init_db():
    """Initialize MongoDB connection with proper indexes"""
    global mongo_client, db, media_col, batch_col, counter_col
    try:
        mongo_client = MongoClient(
            MONGODB_URI,
            tls=True,
            tlsCAFile=certifi.where(),
            serverSelectionTimeoutMS=30000,
        )
        mongo_client.admin.command("ping")
        db = mongo_client[MONGODB_DB]
        media_col = db["media_items"]
        batch_col = db["batches"]
        counter_col = db["counters"]
        
        # Create indexes for fast queries
        media_col.create_index([("item_id", ASCENDING)], unique=True)
        media_col.create_index([("batch_no", ASCENDING)])
        media_col.create_index([("created_at", DESCENDING)])
        media_col.create_index([("file_unique_id", ASCENDING)])
        media_col.create_index([("source_link", ASCENDING)])
        
        batch_col.create_index([("batch_no", ASCENDING)], unique=True)
        batch_col.create_index([("created_at", DESCENDING)])
        
        counter_col.create_index([("_id", ASCENDING)])
        
        logger.info("✅ MongoDB initialized with all indexes")
    except Exception as e:
        logger.exception("❌ MongoDB init failed: %s", e)
        raise

def get_next_item_id() -> str:
    """
    Atomically increment item counter and return next ID
    FIXED: Uses MongoDB's atomic findOneAndUpdate
    """
    result = counter_col.find_one_and_update(
        {"_id": "item_counter"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    seq = result.get("seq", 1)
    return f"a{seq}"

def get_next_batch_no() -> int:
    """
    Atomically increment batch counter and return next batch number
    FIXED: Uses MongoDB's atomic findOneAndUpdate (was: read-modify-write race condition)
    """
    result = counter_col.find_one_and_update(
        {"_id": "batch_counter"},
        {"$inc": {"seq": 1}},
        upsert=True,
        return_document=ReturnDocument.AFTER
    )
    seq = result.get("seq", 1)
    return seq

def update_batch_count(batch_no: int, delta: int):
    """Atomically update batch count"""
    batch_col.update_one(
        {"batch_no": batch_no},
        {
            "$inc": {"count": delta},
            "$set": {"updated_at": datetime.utcnow()}
        },
        upsert=True
    )

def get_batch_info(batch_no: int) -> dict:
    """Get batch metadata"""
    return batch_col.find_one({"batch_no": batch_no})

def get_all_batches(page: int = 1, page_size: int = 100) -> list:
    """Get all batches with pagination"""
    skip = (page - 1) * page_size
    return list(batch_col.find({}).sort("batch_no", ASCENDING).skip(skip).limit(page_size))

def count_batches() -> int:
    """Total number of batches"""
    return batch_col.count_documents({})

def count_items() -> int:
    """Total number of items"""
    return media_col.count_documents({})

def get_items_in_batch(batch_no: int, page: int = 1, page_size: int = 50) -> list:
    """Get items in a batch with pagination"""
    skip = (page - 1) * page_size
    return list(
        media_col.find({"batch_no": batch_no})
        .sort("created_at", ASCENDING)
        .skip(skip)
        .limit(page_size)
    )

def get_item_by_id(item_id: str) -> dict:
    """Get single item by ID"""
    return media_col.find_one({"item_id": item_id})

def insert_media_item(doc: dict) -> str:
    """Insert new media item and return item_id"""
    result = media_col.insert_one(doc)
    return doc["item_id"]

def delete_media_item(item_id: str) -> int:
    """Delete media item and return deleted count"""
    doc = media_col.find_one({"item_id": item_id})
    if doc:
        batch_no = doc.get("batch_no")
        media_col.delete_one({"item_id": item_id})
        update_batch_count(batch_no, -1)
        return 1
    return 0

def get_by_source_link(source_link: str) -> dict:
    """Get item by permanent source link"""
    return media_col.find_one({"source_link": source_link})

def get_by_file_unique_id(file_unique_id: str) -> dict:
    """Get item by permanent file_unique_id"""
    return media_col.find_one({"file_unique_id": file_unique_id})

def search_items(query: dict, page: int = 1, page_size: int = 50) -> list:
    """Search items with query filter"""
    skip = (page - 1) * page_size
    return list(
        media_col.find(query)
        .sort("created_at", DESCENDING)
        .skip(skip)
        .limit(page_size)
    )

def backup_statistics() -> dict:
    """Get database statistics"""
    return {
        "total_items": count_items(),
        "total_batches": count_batches(),
        "timestamp": datetime.utcnow().isoformat()
    }

import io
import json
from datetime import datetime

import database as dbase
from telegram import Update
from telegram.ext import ContextTypes


async def all_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    batches = list(dbase.batch_col.find({}).sort("batch_no", 1))
    items = list(dbase.media_col.find({}, {"_id": 0}).sort([("batch_no", 1), ("created_at", 1)]))

    counts = {}
    for it in items:
        counts[it["batch_no"]] = counts.get(it["batch_no"], 0) + 1
        if isinstance(it.get("created_at"), datetime):
            it["created_at"] = it["created_at"].isoformat() + "Z"

    batch_summary = []
    for b in batches:
        bn = b["batch_no"]
        batch_summary.append({
            "batch_no": bn,
            "count": counts.get(bn, b.get("count", 0))
        })

    payload = {
        "generated_at": datetime.utcnow().isoformat() + "Z",
        "total_batches": len(batch_summary),
        "total_items": len(items),
        "batch_summary": batch_summary,
        "items": items
    }

    bio = io.BytesIO(json.dumps(payload, indent=2, default=str).encode("utf-8"))
    bio.name = "all_batches.json"
    await update.message.reply_document(
        bio,
        caption=f"Total Batches: {len(batch_summary)} | Total Items: {len(items)}"
    )

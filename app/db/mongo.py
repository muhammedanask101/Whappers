import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database


_MONGO_CLIENT: Optional[MongoClient] = None
_SEEDED = False
DEFAULT_BUSINESS_ID = "biz_default"


def get_mongo_client() -> MongoClient:
    global _MONGO_CLIENT
    if _MONGO_CLIENT is None:
        uri = os.getenv("MONGODB_URI", "").strip()
        if not uri:
            raise RuntimeError("MONGODB_URI is not configured.")
        _MONGO_CLIENT = MongoClient(uri, serverSelectionTimeoutMS=5000)
    return _MONGO_CLIENT


def get_mongo_database() -> Database:
    db_name = os.getenv("MONGODB_DB_NAME", "").strip() or "whatsapp_agent"
    database = get_mongo_client()[db_name]
    ensure_seed_data(database)
    return database


def get_collection(name: str) -> Collection:
    return get_mongo_database()[name]


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def to_iso(value: Optional[datetime]) -> Optional[str]:
    if value is None:
        return None
    return value.astimezone(timezone.utc).isoformat()


def parse_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.astimezone(timezone.utc)
    text = str(value).strip()
    if not text:
        return None
    try:
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def generate_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-z0-9]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-")
    return cleaned or "business"


def ensure_seed_data(database: Database) -> None:
    global _SEEDED
    if _SEEDED:
        return

    now = utc_now()
    businesses = database["businesses"]
    default_phone = os.getenv("VENDOR_PHONE", "").strip()
    default_whatsapp = os.getenv("DEFAULT_BUSINESS_WHATSAPP_NUMBER", "").strip() or default_phone
    default_name = os.getenv("DEFAULT_BUSINESS_NAME", "").strip() or "Local Shop"

    businesses.update_one(
        {"business_id": DEFAULT_BUSINESS_ID},
        {
            "$setOnInsert": {
                "business_id": DEFAULT_BUSINESS_ID,
                "slug": slugify(default_name),
                "name": default_name,
                "business_type": "retail",
                "description": "Friendly neighborhood business on WhatsApp.",
                "address": "",
                "hours": "",
                "contact_phone": default_phone,
                "vendor_phone": default_phone,
                "whatsapp_number": default_whatsapp,
                "support_details": "Message us here on WhatsApp for help.",
                "policies": "",
                "delivery_info": "",
                "broadcast_groups": [],
                "active": True,
                "created_at": now,
                "updated_at": now,
            }
        },
        upsert=True,
    )

    _seed_legacy_products(database, now)

    businesses.create_index("business_id", unique=True)
    businesses.create_index("slug", unique=True)
    businesses.create_index("vendor_phone", unique=True, sparse=True)
    businesses.create_index("whatsapp_number", unique=True, sparse=True)

    _drop_legacy_indexes(database)

    database["products"].create_index([("business_id", 1), ("name", 1)], unique=True)
    database["products"].create_index([("business_id", 1), ("category", 1)])
    database["products"].create_index([("business_id", 1), ("tags", 1)])
    database["orders"].create_index("order_id", unique=True)
    database["orders"].create_index([("business_id", 1), ("customer_phone", 1)])
    database["orders"].create_index([("business_id", 1), ("created_at", -1)])
    database["conversation_state"].create_index([("business_id", 1), ("phone", 1)], unique=True)
    database["chat_history"].create_index([("business_id", 1), ("phone", 1), ("created_at", -1)])
    database["conversation_summaries"].create_index([("business_id", 1), ("phone", 1)], unique=True)
    database["customer_learning"].create_index([("business_id", 1), ("phone", 1)], unique=True)
    database["semantic_memory"].create_index([("business_id", 1), ("phone", 1), ("created_at", -1)])
    database["agent_traces"].create_index([("business_id", 1), ("phone", 1), ("created_at", -1)])
    database["performance_metrics"].create_index([("business_id", 1), ("metric", 1), ("created_at", -1)])
    database["tool_policy_stats"].create_index([("business_id", 1), ("tool", 1)], unique=True)
    database["interaction_events"].create_index([("business_id", 1), ("phone", 1), ("created_at", -1)])
    database["interaction_events"].create_index([("business_id", 1), ("kind", 1), ("created_at", -1)])
    database["automation_log"].create_index([("business_id", 1), ("phone", 1), ("created_at", -1)])
    database["automation_runs"].create_index([("business_id", 1), ("created_at", -1)])
    database["vendor_notification_queue"].create_index([("business_id", 1), ("order_id", 1)], unique=True)
    database["promotions"].create_index([("business_id", 1), ("promotion_id", 1)], unique=True)
    database["events"].create_index([("business_id", 1), ("event_id", 1)], unique=True)
    database["support_tickets"].create_index([("business_id", 1), ("ticket_id", 1)], unique=True)
    database["support_tickets"].create_index([("business_id", 1), ("customer_phone", 1)])
    database["customer_profiles"].create_index([("business_id", 1), ("phone", 1)], unique=True)
    database["manager_users"].create_index("email", unique=True)
    _SEEDED = True


def _seed_legacy_products(database: Database, now: datetime) -> None:
    products = database["products"]
    if products.count_documents({"business_id": DEFAULT_BUSINESS_ID}, limit=1) > 0:
        return

    legacy_products = list(products.find({"business_id": {"$exists": False}}).limit(1))
    if legacy_products:
        products.update_many(
            {"business_id": {"$exists": False}},
            {"$set": {"business_id": DEFAULT_BUSINESS_ID, "updated_at": now}},
        )
        return

    seed_products = [
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "coffee",
            "display_name": "Coffee",
            "price": 80.0,
            "unit": "cup",
            "category": "drink",
            "description": "Freshly brewed hot coffee.",
            "tags": ["coffee", "hot", "beverage"],
            "attributes": {"serving": "hot"},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "tea",
            "display_name": "Tea",
            "price": 20.0,
            "unit": "cup",
            "category": "drink",
            "description": "Strong local tea.",
            "tags": ["tea", "hot", "beverage"],
            "attributes": {"serving": "hot"},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "juice",
            "display_name": "Juice",
            "price": 60.0,
            "unit": "glass",
            "category": "drink",
            "description": "Refreshing fruit juice.",
            "tags": ["juice", "cold", "beverage"],
            "attributes": {"serving": "cold"},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "coke",
            "display_name": "Coke",
            "price": 45.0,
            "unit": "bottle",
            "category": "drink",
            "description": "Chilled soft drink.",
            "tags": ["soft drink", "cold", "bottle"],
            "attributes": {"serving": "cold"},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "sandwich",
            "display_name": "Sandwich",
            "price": 120.0,
            "unit": "piece",
            "category": "snack",
            "description": "Freshly prepared sandwich.",
            "tags": ["snack", "bread"],
            "attributes": {},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
        {
            "business_id": DEFAULT_BUSINESS_ID,
            "name": "banana chips",
            "display_name": "Banana Chips",
            "price": 140.0,
            "unit": "pack",
            "category": "snack",
            "description": "Crispy banana chips.",
            "tags": ["snack", "chips", "banana"],
            "attributes": {},
            "active": True,
            "offer_price": None,
            "offer_until": None,
            "created_at": now,
            "updated_at": now,
        },
    ]
    products.insert_many(seed_products)


def _drop_legacy_indexes(database: Database) -> None:
    _drop_index_if_exists(database["conversation_state"], "phone_1")
    _drop_index_if_exists(database["products"], "name_1")
    _drop_index_if_exists(database["vendor_notification_queue"], "order_id_1")
    _drop_index_if_exists(database["promotions"], "promotion_id_1")
    _drop_index_if_exists(database["events"], "event_id_1")
    _drop_index_if_exists(database["support_tickets"], "ticket_id_1")


def _drop_index_if_exists(collection: Collection, index_name: str) -> None:
    try:
        existing = collection.index_information()
        if index_name in existing:
            collection.drop_index(index_name)
    except Exception:
        pass

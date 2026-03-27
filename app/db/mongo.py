import os
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from pymongo import MongoClient
from pymongo.collection import Collection
from pymongo.database import Database


_MONGO_CLIENT: Optional[MongoClient] = None
_SEEDED = False


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


def ensure_seed_data(database: Database) -> None:
    global _SEEDED
    if _SEEDED:
        return

    now = utc_now()
    products = database["products"]
    if products.count_documents({}) == 0:
        seed_products: List[Dict[str, Any]] = [
            {
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

    business_profile = database["business_profile"]
    if business_profile.count_documents({"profile_id": "default"}, limit=1) == 0:
        business_profile.insert_one(
            {
                "profile_id": "default",
                "name": "Local Shop",
                "business_type": "retail",
                "description": "Friendly neighborhood business on WhatsApp.",
                "address": "",
                "hours": "",
                "contact_phone": os.getenv("VENDOR_PHONE", "").strip(),
                "support_details": "Message us here on WhatsApp for help.",
                "policies": "",
                "delivery_info": "",
                "broadcast_groups": [],
                "created_at": now,
                "updated_at": now,
            }
        )

    products.create_index("name", unique=True)
    products.create_index("category")
    products.create_index("tags")
    database["orders"].create_index("order_id", unique=True)
    database["orders"].create_index("customer_phone")
    database["orders"].create_index("created_at")
    database["conversation_state"].create_index("phone", unique=True)
    database["chat_history"].create_index([("phone", 1), ("created_at", -1)])
    database["vendor_notification_queue"].create_index("order_id", unique=True)
    database["promotions"].create_index("promotion_id", unique=True)
    database["events"].create_index("event_id", unique=True)
    database["support_tickets"].create_index("ticket_id", unique=True)
    database["support_tickets"].create_index("customer_phone")
    database["business_profile"].create_index("profile_id", unique=True)
    _SEEDED = True

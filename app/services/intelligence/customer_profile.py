from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List

from app.db.mongo import get_collection, utc_now
from app.services.agent.ranking import recency_decay, summarize_affinity


def get_customer_profile(phone: str, business_id: str) -> Dict[str, Any]:
    profile = get_collection("customer_profiles").find_one(
        {"phone": phone, "business_id": business_id}
    )
    if profile:
        return profile
    return _empty_profile(phone, business_id)


def update_customer_profile(phone: str, business_id: str, order: Dict[str, Any]) -> None:
    orders_collection = get_collection("orders")
    historical_orders = list(
        orders_collection.find({"business_id": business_id, "customer_phone": phone}).sort("created_at", -1).limit(60)
    )
    preview_orders = historical_orders + [_preview_order(order)]
    profile = _build_profile(phone, business_id, preview_orders)
    get_collection("customer_profiles").update_one(
        {"phone": phone, "business_id": business_id},
        {"$set": profile},
        upsert=True,
    )


def refresh_customer_profile(phone: str, business_id: str) -> Dict[str, Any]:
    orders = list(
        get_collection("orders").find({"business_id": business_id, "customer_phone": phone}).sort("created_at", -1).limit(80)
    )
    profile = _build_profile(phone, business_id, orders)
    get_collection("customer_profiles").update_one(
        {"phone": phone, "business_id": business_id},
        {"$set": profile},
        upsert=True,
    )
    return profile


def update_last_active(phone: str, business_id: str) -> None:
    get_collection("customer_profiles").update_one(
        {"phone": phone, "business_id": business_id},
        {"$set": {"last_active_at": utc_now(), "updated_at": utc_now()}},
        upsert=True,
    )


def get_segment_snapshot(business_id: str) -> Dict[str, Any]:
    rows = list(get_collection("customer_profiles").find({"business_id": business_id}))
    segment_counts = Counter(row.get("segment", "unknown") for row in rows)
    top_value = sorted(rows, key=lambda row: float(row.get("total_spent", 0.0)), reverse=True)[:10]
    return {
        "segment_counts": dict(segment_counts),
        "top_customers": [
            {
                "phone": row.get("phone"),
                "segment": row.get("segment"),
                "total_spent": float(row.get("total_spent", 0.0)),
            }
            for row in top_value
        ],
    }


def _build_profile(phone: str, business_id: str, orders: Iterable[Dict[str, Any]]) -> Dict[str, Any]:
    order_list = list(orders)
    total_orders = len(order_list)
    total_spent = sum(float(order.get("total", 0.0)) for order in order_list)
    avg_order_value = total_spent / total_orders if total_orders else 0.0
    favorite_products = _favorite_products(order_list)
    category_affinity = _category_affinity(order_list)
    product_affinity = _product_affinity(order_list)
    rfm = _rfm(order_list, total_spent)
    segment = _segment_from_rfm(rfm)
    support_risk = _support_risk(phone, business_id)
    cohorts = _cohorts(order_list)
    recent_intents = _recent_intents(phone, business_id)

    last_order_at = order_list[0].get("created_at") if order_list else None
    profile = {
        "phone": phone,
        "business_id": business_id,
        "total_orders": total_orders,
        "total_spent": round(total_spent, 2),
        "avg_order_value": round(avg_order_value, 2),
        "favorite_products": favorite_products,
        "category_affinity": category_affinity,
        "product_affinity": product_affinity,
        "rfm": rfm,
        "segment": segment,
        "cohorts": cohorts,
        "support_risk": support_risk,
        "recent_intents": recent_intents,
        "last_order_at": last_order_at or None,
        "last_active_at": utc_now(),
        "updated_at": utc_now(),
    }
    return profile


def _empty_profile(phone: str, business_id: str) -> Dict[str, Any]:
    return {
        "phone": phone,
        "business_id": business_id,
        "total_orders": 0,
        "total_spent": 0.0,
        "avg_order_value": 0.0,
        "favorite_products": [],
        "category_affinity": {},
        "product_affinity": {},
        "rfm": {"recency": 0.0, "frequency": 0.0, "monetary": 0.0},
        "segment": "new",
        "cohorts": {"lifecycle": "new"},
        "support_risk": 0.0,
        "recent_intents": [],
        "last_order_at": None,
        "last_active_at": utc_now(),
        "updated_at": utc_now(),
    }


def _favorite_products(orders: List[Dict[str, Any]]) -> List[str]:
    counts = Counter()
    for order in orders:
        for item in order.get("items", []):
            counts[str(item.get("name", ""))] += int(item.get("qty", 1))
    return [name for name, _ in counts.most_common(6)]


def _category_affinity(orders: List[Dict[str, Any]]) -> Dict[str, float]:
    catalog = {
        row["name"]: row.get("category", "other")
        for row in get_collection("products").find({}, {"name": 1, "category": 1})
    }
    counts = Counter()
    total = 0
    for order in orders:
        for item in order.get("items", []):
            qty = int(item.get("qty", 1))
            counts[catalog.get(item.get("name"), "other")] += qty
            total += qty
    if total == 0:
        return {}
    return {category: round(count / total, 4) for category, count in counts.items()}


def _product_affinity(orders: List[Dict[str, Any]]) -> Dict[str, float]:
    counts = Counter()
    total = 0
    for order in orders:
        for item in order.get("items", []):
            qty = int(item.get("qty", 1))
            counts[str(item.get("name", ""))] += qty
            total += qty
    if total == 0:
        return {}
    affinity = {name: round(count / total, 4) for name, count in counts.items()}
    affinity.update(summarize_affinity(orders))
    return affinity


def _rfm(orders: List[Dict[str, Any]], total_spent: float) -> Dict[str, float]:
    if not orders:
        return {"recency": 0.0, "frequency": 0.0, "monetary": 0.0}
    recency = recency_decay(orders[0].get("created_at"), half_life_days=21.0)
    frequency = min(1.0, len(orders) / 12.0)
    monetary = min(1.0, total_spent / 15000.0)
    return {"recency": round(recency, 4), "frequency": round(frequency, 4), "monetary": round(monetary, 4)}


def _segment_from_rfm(rfm: Dict[str, float]) -> str:
    score = rfm["recency"] * 0.4 + rfm["frequency"] * 0.3 + rfm["monetary"] * 0.3
    if score >= 0.72:
        return "vip"
    if score >= 0.46:
        return "repeat"
    if score >= 0.2:
        return "at_risk"
    return "new"


def _support_risk(phone: str, business_id: str) -> float:
    tickets = list(get_collection("support_tickets").find({"business_id": business_id, "customer_phone": phone}).sort("created_at", -1).limit(10))
    if not tickets:
        return 0.0
    unresolved = sum(1 for ticket in tickets if ticket.get("status") != "resolved")
    recent = sum(recency_decay(ticket.get("created_at"), half_life_days=30.0) for ticket in tickets)
    return round(min(1.0, unresolved * 0.25 + recent * 0.2), 4)


def _cohorts(orders: List[Dict[str, Any]]) -> Dict[str, str]:
    if not orders:
        return {"lifecycle": "new"}
    created = orders[-1].get("created_at")
    first_order = _safe_datetime(created)
    if not first_order:
        return {"lifecycle": "active"}
    now = datetime.now(timezone.utc)
    age_days = (now - first_order).days
    if age_days <= 30:
        lifecycle = "onboarding"
    elif age_days <= 120:
        lifecycle = "growing"
    else:
        lifecycle = "mature"
    return {"lifecycle": lifecycle}


def _recent_intents(phone: str, business_id: str) -> List[str]:
    events = list(
        get_collection("interaction_events")
        .find({"business_id": business_id, "phone": phone, "kind": "agent_reply"})
        .sort("created_at", -1)
        .limit(8)
    )
    intents = [str(event.get("payload", {}).get("intent") or "") for event in events]
    return [intent for intent in intents if intent]


def _preview_order(order: Dict[str, Any]) -> Dict[str, Any]:
    preview = dict(order)
    preview.setdefault("created_at", utc_now())
    return preview


def _safe_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None

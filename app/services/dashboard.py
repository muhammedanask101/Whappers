from __future__ import annotations

from collections import Counter
from typing import Any, Optional

from app.db.mongo import get_collection, parse_datetime, to_iso, utc_now
from app.services.agent.observability import get_recent_traces
from app.services.auth import get_subscription_for_business
from app.services.intelligence.analytics import get_business_metrics, get_top_products
from app.services.intelligence.insights import generate_insights
from app.services.vendor import (
    add_product,
    create_business,
    delete_product,
    get_business_by_id,
    get_product_catalog,
    list_businesses,
    summarize_business,
    update_business,
    update_product,
)


def get_admin_overview() -> dict[str, Any]:
    businesses = list_businesses()
    order_rows = list(get_collection("orders").find({}))
    total_revenue = round(sum(float(row.get("total", 0.0)) for row in order_rows), 2)
    status_counts = Counter(str(row.get("status") or "unknown") for row in order_rows)
    return {
        "businesses": len(businesses),
        "active_businesses": sum(1 for business in businesses if business.get("active", True)),
        "orders": len(order_rows),
        "revenue": total_revenue,
        "vendors": sum(1 for business in businesses if business.get("vendor_phone")),
        "support_open": get_collection("support_tickets").count_documents({"status": {"$ne": "resolved"}}),
        "order_status_breakdown": dict(status_counts),
        "top_products": get_global_top_products(),
        "recent_activity_count": get_collection("interaction_events").count_documents({}),
    }


def get_business_list_with_context() -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for business in list_businesses():
        business_id = business["business_id"]
        results.append(
            {
                **business,
                "summary": summarize_business(business_id),
                "subscription": get_subscription_for_business(business_id),
                "onboarding": get_onboarding_state(business_id),
            }
        )
    return results


def get_business_detail_with_context(business_id: str) -> Optional[dict[str, Any]]:
    business = get_business_by_id(business_id)
    if not business:
        return None
    return {
        **business,
        "summary": summarize_business(business_id),
        "subscription": get_subscription_for_business(business_id),
        "onboarding": get_onboarding_state(business_id),
        "catalog": get_product_catalog(business_id=business_id, limit=100),
        "recent_orders": query_orders(business_id=business_id, limit=20),
    }


def query_orders(
    *,
    business_id: str = "",
    status: str = "",
    date_from: str = "",
    date_to: str = "",
    limit: int = 200,
) -> list[dict[str, Any]]:
    query: dict[str, Any] = {}
    if business_id:
        query["business_id"] = business_id
    if status:
        query["status"] = status
    date_query: dict[str, Any] = {}
    parsed_from = parse_datetime(date_from) if date_from else None
    parsed_to = parse_datetime(date_to) if date_to else None
    if parsed_from:
        date_query["$gte"] = parsed_from
    if parsed_to:
        date_query["$lte"] = parsed_to
    if date_query:
        query["created_at"] = date_query
    rows = get_collection("orders").find(query).sort("created_at", -1).limit(min(max(int(limit or 200), 1), 500))
    return [
        {
            "business_id": row.get("business_id"),
            "order_id": row.get("order_id"),
            "customer_phone": row.get("customer_phone"),
            "status": row.get("status"),
            "items": row.get("items", []),
            "line_items": row.get("line_items", []),
            "total": float(row.get("total", 0.0)),
            "address": row.get("address", ""),
            "created_at": to_iso(parse_datetime(row.get("created_at"))),
            "updated_at": to_iso(parse_datetime(row.get("updated_at"))),
        }
        for row in rows
    ]


def get_business_analytics(business_id: str) -> dict[str, Any]:
    metrics = get_business_metrics(business_id)
    top_products = get_top_products(business_id)
    orders = query_orders(business_id=business_id, limit=500)
    return {
        "metrics": metrics,
        "top_products": top_products,
        "order_trends": _order_trends(orders),
        "insights": generate_insights(business_id),
    }


def get_global_analytics() -> dict[str, Any]:
    order_rows = query_orders(limit=500)
    revenue_by_business = Counter()
    for row in order_rows:
        revenue_by_business[str(row.get("business_id") or "")] += float(row.get("total", 0.0))
    return {
        "overview": get_admin_overview(),
        "revenue_by_business": [{"business_id": business_id, "revenue": round(amount, 2)} for business_id, amount in revenue_by_business.most_common()],
        "order_trends": _order_trends(order_rows),
        "top_products": get_global_top_products(),
    }


def get_global_top_products(limit: int = 10) -> list[dict[str, Any]]:
    counter = Counter()
    revenue = Counter()
    for row in get_collection("orders").find({}):
        for item in row.get("line_items", []):
            name = str(item.get("display_name") or item.get("name") or "")
            qty = int(item.get("qty", 1))
            counter[name] += qty
            revenue[name] += float(item.get("line_total", 0.0))
    return [
        {"name": name, "quantity": quantity, "revenue": round(float(revenue.get(name, 0.0)), 2)}
        for name, quantity in counter.most_common(limit)
    ]


def list_vendor_activity(business_id: str = "") -> list[dict[str, Any]]:
    businesses = [get_business_by_id(business_id)] if business_id else list_businesses()
    results = []
    for business in businesses:
        if not business:
            continue
        current_business_id = business["business_id"]
        results.append(
            {
                "business_id": current_business_id,
                "business_name": business.get("name"),
                "vendor_phone": business.get("vendor_phone"),
                "whatsapp_number": business.get("whatsapp_number"),
                "activity": {
                    "orders_total": get_collection("orders").count_documents({"business_id": current_business_id}),
                    "orders_pending": get_collection("orders").count_documents({"business_id": current_business_id, "status": "pending"}),
                    "support_open": get_collection("support_tickets").count_documents({"business_id": current_business_id, "status": {"$ne": "resolved"}}),
                    "queued_notifications": get_collection("vendor_notification_queue").count_documents({"business_id": current_business_id, "status": "pending"}),
                    "last_order_at": _latest_timestamp("orders", {"business_id": current_business_id}),
                    "last_trace_at": _latest_timestamp("agent_traces", {"business_id": current_business_id, "phone": business.get("vendor_phone")}),
                },
            }
        )
    return results


def get_activity_logs(business_id: str, phone: str = "") -> dict[str, Any]:
    traces = get_recent_traces(business_id, phone=phone, limit=40)
    event_query: dict[str, Any] = {"business_id": business_id}
    if phone:
        event_query["phone"] = phone
    events = list(get_collection("interaction_events").find(event_query).sort("created_at", -1).limit(50))
    automation = list(get_collection("automation_log").find(event_query).sort("created_at", -1).limit(50))
    return {
        "agent_traces": [
            {
                "phone": row.get("phone"),
                "role": row.get("role"),
                "message": row.get("message"),
                "analysis": row.get("analysis", {}),
                "strategy": row.get("strategy", {}),
                "metrics": row.get("metrics", {}),
                "created_at": to_iso(parse_datetime(row.get("created_at"))),
            }
            for row in traces
        ],
        "events": [
            {
                "kind": row.get("kind"),
                "phone": row.get("phone"),
                "payload": row.get("payload", {}),
                "created_at": to_iso(parse_datetime(row.get("created_at"))),
            }
            for row in events
        ],
        "automation_runs": [
            {
                "phone": row.get("phone"),
                "status": row.get("status"),
                "action": row.get("action"),
                "created_at": to_iso(parse_datetime(row.get("created_at"))),
            }
            for row in automation
        ],
    }


def upsert_subscription(business_id: str, *, plan: str, status: str, current_period_end: str = "") -> dict[str, Any]:
    normalized_plan = plan.strip().lower()
    if normalized_plan not in {"basic", "pro", "enterprise"}:
        raise ValueError("Unsupported subscription plan.")
    normalized_status = status.strip().lower() or "active"
    expires_at = parse_datetime(current_period_end) if current_period_end else utc_now()
    get_collection("subscriptions").update_one(
        {"business_id": business_id},
        {
            "$set": {
                "business_id": business_id,
                "plan": normalized_plan,
                "status": normalized_status,
                "current_period_end": expires_at,
                "updated_at": utc_now(),
            },
            "$setOnInsert": {"created_at": utc_now()},
        },
        upsert=True,
    )
    return get_subscription_for_business(business_id)


def get_onboarding_state(business_id: str) -> dict[str, Any]:
    document = get_collection("onboarding_states").find_one({"business_id": business_id})
    if document:
        return {
            "business_id": business_id,
            "step": document.get("step", "signup"),
            "completed_steps": document.get("completed_steps", []),
            "is_complete": bool(document.get("is_complete", False)),
            "updated_at": to_iso(parse_datetime(document.get("updated_at"))),
        }
    return {"business_id": business_id, "step": "signup", "completed_steps": [], "is_complete": False, "updated_at": None}


def update_onboarding_state(business_id: str, *, step: str, mark_complete: bool = False) -> dict[str, Any]:
    get_collection("onboarding_states").update_one(
        {"business_id": business_id},
        {
            "$set": {"business_id": business_id, "step": step, "is_complete": mark_complete, "updated_at": utc_now()},
            "$addToSet": {"completed_steps": step},
            "$setOnInsert": {"created_at": utc_now()},
        },
        upsert=True,
    )
    return get_onboarding_state(business_id)


def create_business_with_subscription(payload: dict[str, Any]) -> dict[str, Any]:
    business = create_business(
        name=str(payload.get("name") or "").strip(),
        slug=str(payload.get("slug") or "").strip() or None,
        whatsapp_number=str(payload.get("whatsapp_number") or "").strip(),
        vendor_phone=str(payload.get("vendor_phone") or "").strip(),
        business_type=str(payload.get("business_type") or "retail").strip(),
        description=str(payload.get("description") or "").strip(),
        address=str(payload.get("address") or "").strip(),
        hours=str(payload.get("hours") or "").strip(),
        contact_phone=str(payload.get("contact_phone") or "").strip(),
        support_details=str(payload.get("support_details") or "").strip(),
        policies=str(payload.get("policies") or "").strip(),
        delivery_info=str(payload.get("delivery_info") or "").strip(),
    )
    upsert_subscription(
        business["business_id"],
        plan=str(payload.get("plan") or "basic"),
        status=str(payload.get("subscription_status") or "trialing"),
        current_period_end=str(payload.get("current_period_end") or ""),
    )
    update_onboarding_state(business["business_id"], step="business_info")
    return get_business_detail_with_context(business["business_id"]) or business


def update_business_with_context(business_id: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    updated = update_business(
        business_id,
        name=payload.get("name"),
        slug=payload.get("slug"),
        whatsapp_number=payload.get("whatsapp_number"),
        vendor_phone=payload.get("vendor_phone"),
        business_type=payload.get("business_type"),
        description=payload.get("description"),
        address=payload.get("address"),
        hours=payload.get("hours"),
        contact_phone=payload.get("contact_phone"),
        support_details=payload.get("support_details"),
        policies=payload.get("policies"),
        delivery_info=payload.get("delivery_info"),
        active=payload.get("active"),
    )
    if not updated:
        return None
    if payload.get("whatsapp_number"):
        update_onboarding_state(business_id, step="whatsapp_number")
    return get_business_detail_with_context(business_id)


def add_catalog_product(business_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    product = add_product(
        business_id=business_id,
        name=str(payload.get("name") or "").strip(),
        price=float(payload.get("price") or 0),
        unit=str(payload.get("unit") or "unit"),
        category=str(payload.get("category") or "").strip() or None,
        description=str(payload.get("description") or "").strip(),
        tags=list(payload.get("tags") or []),
        attributes=dict(payload.get("attributes") or {}),
    )
    update_onboarding_state(business_id, step="catalog")
    return product


def update_catalog_product(business_id: str, product_name: str, payload: dict[str, Any]) -> Optional[dict[str, Any]]:
    return update_product(
        business_id=business_id,
        name=product_name,
        price=float(payload["price"]) if payload.get("price") is not None else None,
        unit=payload.get("unit"),
        category=payload.get("category"),
        description=payload.get("description"),
        tags=list(payload.get("tags")) if payload.get("tags") is not None else None,
        attributes=dict(payload.get("attributes")) if payload.get("attributes") is not None else None,
        active=payload.get("active"),
    )


def remove_catalog_product(business_id: str, product_name: str) -> bool:
    return delete_product(product_name, business_id=business_id)


def _latest_timestamp(collection_name: str, query: dict[str, Any]) -> Optional[str]:
    row = get_collection(collection_name).find_one(query, sort=[("created_at", -1)])
    return to_iso(parse_datetime(row.get("created_at"))) if row else None


def _order_trends(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    buckets = Counter()
    revenue = Counter()
    for row in orders:
        created = parse_datetime(row.get("created_at"))
        if not created:
            continue
        key = created.strftime("%Y-%m-%d")
        buckets[key] += 1
        revenue[key] += float(row.get("total", 0.0))
    return [
        {"date": date_key, "orders": buckets[date_key], "revenue": round(float(revenue[date_key]), 2)}
        for date_key in sorted(buckets.keys())
    ]

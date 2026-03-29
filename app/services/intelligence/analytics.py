from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List

from app.db.mongo import get_collection
from app.services.intelligence.customer_profile import get_segment_snapshot


def get_business_metrics(business_id: str) -> Dict[str, Any]:
    orders = list(get_collection("orders").find({"business_id": business_id}))
    customers = list(get_collection("customer_profiles").find({"business_id": business_id}))
    tickets = list(get_collection("support_tickets").find({"business_id": business_id}))

    total_orders = len(orders)
    total_revenue = sum(float(order.get("total", 0.0)) for order in orders)
    avg_order_value = total_revenue / total_orders if total_orders else 0.0
    open_tickets = sum(1 for ticket in tickets if ticket.get("status") != "resolved")

    return {
        "total_orders": total_orders,
        "total_revenue": round(total_revenue, 2),
        "avg_order_value": round(avg_order_value, 2),
        "total_customers": len(customers),
        "open_tickets": open_tickets,
        "segment_snapshot": get_segment_snapshot(business_id),
        "performance": _performance_metrics(business_id),
    }


def get_top_products(business_id: str) -> List[Dict[str, Any]]:
    orders = list(get_collection("orders").find({"business_id": business_id}))
    counter = Counter()
    revenue = Counter()

    for order in orders:
        line_items = order.get("line_items", [])
        if line_items:
            for item in line_items:
                name = str(item.get("name", ""))
                qty = int(item.get("qty", 1))
                counter[name] += qty
                revenue[name] += float(item.get("line_total", 0.0))
            continue
        for item in order.get("items", []):
            name = str(item.get("name", ""))
            counter[name] += int(item.get("qty", 1))

    return [
        {
            "name": name,
            "quantity": quantity,
            "revenue": round(float(revenue.get(name, 0.0)), 2),
        }
        for name, quantity in counter.most_common(8)
    ]


def _performance_metrics(business_id: str) -> Dict[str, Any]:
    rows = list(get_collection("performance_metrics").find({"business_id": business_id}).sort("created_at", -1).limit(1000))
    aggregated = Counter()
    for row in rows:
        aggregated[str(row.get("metric") or "")] += float(row.get("amount", 0.0))
    agent_runs = max(float(aggregated.get("agent_runs", 0.0)), 1.0)
    return {
        "agent_runs": int(aggregated.get("agent_runs", 0.0)),
        "goals_completed": int(aggregated.get("goal_completed", 0.0)),
        "order_completion_rate": round(float(aggregated.get("order_completion", 0.0)) / agent_runs, 4),
        "support_handled": int(aggregated.get("support_handled", 0.0)),
        "avg_uncertainty": round(float(aggregated.get("agent_uncertainty", 0.0)) / agent_runs, 4),
    }

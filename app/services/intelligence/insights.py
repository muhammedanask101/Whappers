from __future__ import annotations

from typing import Any, Dict, List

from app.db.mongo import get_collection
from app.services.intelligence.analytics import get_business_metrics, get_top_products


def generate_insights(business_id: str) -> List[Dict[str, Any]]:
    metrics = get_business_metrics(business_id)
    insights: List[Dict[str, Any]] = []

    segment_counts = metrics.get("segment_snapshot", {}).get("segment_counts", {})
    at_risk = int(segment_counts.get("at_risk", 0))
    vip = int(segment_counts.get("vip", 0))
    open_tickets = int(metrics.get("open_tickets", 0))

    if at_risk >= 3:
        insights.append(
            {
                "type": "retention",
                "message": f"{at_risk} customers are drifting. Target them with reactivation messages and personalized offers.",
            }
        )

    if vip:
        insights.append(
            {
                "type": "value",
                "message": f"{vip} VIP customers are active. Prioritize premium service and exclusive product drops.",
            }
        )

    if open_tickets:
        insights.append(
            {
                "type": "support",
                "message": f"There are {open_tickets} open support tickets. Service recovery should take priority over promotion.",
            }
        )

    top_products = get_top_products(business_id)
    if top_products:
        top = top_products[0]
        insights.append(
            {
                "type": "product",
                "message": f"{top['name']} is leading demand. Consider bundling it with adjacent products.",
            }
        )

    total_orders = int(metrics.get("total_orders", 0))
    if total_orders < 5:
        insights.append(
            {
                "type": "growth",
                "message": "Order volume is still low. Use automation and personalized recommendations to improve conversion.",
            }
        )

    return insights

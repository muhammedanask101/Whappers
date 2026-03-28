from datetime import datetime, timedelta

from app.db.mongo import get_collection


def generate_insights(business_id: str) -> list:
    insights = []

    customers = list(get_collection("customer_profiles").find({
        "business_id": business_id
    }))

    # 🔥 Inactive customers
    inactive = [
        c for c in customers
        if c.get("last_active_at") and
        (datetime.utcnow() - c["last_active_at"]).days > 2
    ]

    if len(inactive) > 5:
        insights.append({
            "type": "warning",
            "message": f"{len(inactive)} customers are inactive. Consider sending a campaign."
        })

    # 🔥 VIP customers
    vip = [c for c in customers if c.get("segment") == "vip"]

    if vip:
        insights.append({
            "type": "opportunity",
            "message": f"You have {len(vip)} VIP customers. Offer them exclusive deals."
        })

    # 🔥 Low orders
    orders_count = get_collection("orders").count_documents({
        "businessId": business_id
    })

    if orders_count < 5:
        insights.append({
            "type": "growth",
            "message": "Low order volume. Consider running promotions."
        })

    return insights
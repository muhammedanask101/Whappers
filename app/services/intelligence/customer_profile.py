from typing import Dict, List
from collections import Counter

from app.db.mongo import get_collection, utc_now


def get_customer_profile(phone: str, business_id: str) -> Dict:
    profile = get_collection("customer_profiles").find_one({
        "phone": phone,
        "business_id": business_id
    })

    if not profile:
        return {
            "phone": phone,
            "business_id": business_id,
            "total_orders": 0,
            "total_spent": 0.0,
            "avg_order_value": 0.0,
            "favorite_products": [],
            "segment": "new"
        }

    return profile


def update_customer_profile(phone: str, business_id: str, order: Dict):
    collection = get_collection("customer_profiles")

    items = order.get("items", [])
    total = float(order.get("total", 0))

    product_names = [item["name"] for item in items]

    profile = collection.find_one({
        "phone": phone,
        "business_id": business_id
    })

    if not profile:
        collection.insert_one({
            "phone": phone,
            "business_id": business_id,
            "total_orders": 1,
            "total_spent": total,
            "avg_order_value": total,
            "favorite_products": product_names,
            "last_order_at": utc_now(),
            "last_active_at": utc_now(),
            "segment": "new"
        })
        return

    total_orders = profile.get("total_orders", 0) + 1
    total_spent = profile.get("total_spent", 0.0) + total
    avg_order_value = total_spent / total_orders

    # merge favorites
    existing_products = profile.get("favorite_products", [])
    all_products = existing_products + product_names
    top_products = [p for p, _ in Counter(all_products).most_common(5)]

    segment = _calculate_segment(total_orders, total_spent)

    collection.update_one(
        {"_id": profile["_id"]},
        {
            "$set": {
                "total_orders": total_orders,
                "total_spent": total_spent,
                "avg_order_value": avg_order_value,
                "favorite_products": top_products,
                "last_order_at": utc_now(),
                "last_active_at": utc_now(),
                "segment": segment
            }
        }
    )


def update_last_active(phone: str, business_id: str):
    get_collection("customer_profiles").update_one(
        {"phone": phone, "business_id": business_id},
        {
            "$set": {
                "last_active_at": utc_now()
            }
        },
        upsert=True
    )


def _calculate_segment(total_orders: int, total_spent: float) -> str:
    if total_orders >= 10 or total_spent > 5000:
        return "vip"
    if total_orders >= 3:
        return "repeat"
    return "new"
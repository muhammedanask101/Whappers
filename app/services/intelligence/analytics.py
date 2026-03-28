from app.db.mongo import get_collection


def get_business_metrics(business_id: str) -> dict:
    orders = list(get_collection("orders").find({"businessId": business_id}))
    customers = list(get_collection("customer_profiles").find({"business_id": business_id}))

    total_orders = len(orders)
    total_revenue = sum(o.get("total", 0) for o in orders)

    avg_order_value = total_revenue / total_orders if total_orders else 0

    return {
        "total_orders": total_orders,
        "total_revenue": total_revenue,
        "avg_order_value": avg_order_value,
        "total_customers": len(customers)
    }


def get_top_products(business_id: str) -> list:
    orders = list(get_collection("orders").find({"businessId": business_id}))

    product_count = {}

    for order in orders:
        for item in order.get("items", []):
            name = item["name"]
            product_count[name] = product_count.get(name, 0) + item["qty"]

    sorted_products = sorted(product_count.items(), key=lambda x: x[1], reverse=True)

    return [
        {"name": name, "quantity": qty}
        for name, qty in sorted_products[:5]
    ]
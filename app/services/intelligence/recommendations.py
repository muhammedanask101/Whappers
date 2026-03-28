from typing import List, Dict

from app.services.intelligence.customer_profile import get_customer_profile
from app.services.vendor import get_product_catalog


def recommend_products(phone: str, business_id: str) -> List[Dict]:
    profile = get_customer_profile(phone, business_id)

    catalog = get_product_catalog(business_id=business_id, limit=20)

    if not catalog:
        return []

    # 1. Favorite-based recommendations
    favorites = profile.get("favorite_products", [])

    recommended = []

    if favorites:
        for product in catalog:
            if product["name"] in favorites:
                recommended.append(product)

    # 2. Fallback → top products
    if not recommended:
        recommended = catalog[:5]

    return recommended[:5]


def get_upsell_suggestion(cart_items: List[Dict], business_id: str) -> Dict:
    catalog = get_product_catalog(business_id=business_id, limit=20)

    if not cart_items or not catalog:
        return {}

    cart_names = [item["name"] for item in cart_items]

    # Simple logic: suggest something NOT in cart
    for product in catalog:
        if product["name"] not in cart_names:
            return product

    return {}
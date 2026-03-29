from __future__ import annotations

from typing import Any, Dict, List

from app.services.agent.learning import get_learning_profile
from app.services.agent.ranking import rank_products
from app.services.intelligence.customer_profile import get_customer_profile
from app.services.vendor import get_product_catalog, normalize_product_name


def recommend_products(phone: str, business_id: str, *, query: str = "", exclude: List[str] | None = None) -> List[Dict[str, Any]]:
    profile = get_customer_profile(phone, business_id)
    learning = get_learning_profile(phone, business_id)
    catalog = get_product_catalog(business_id=business_id, limit=50)
    excluded = {normalize_product_name(item) for item in (exclude or [])}
    candidates = [product for product in catalog if product.get("name") not in excluded]
    ranked = rank_products(
        query or "recommended products",
        candidates,
        profile=profile,
        learning_profile=learning,
        include_exploration=True,
    )
    stats = learning.get("recommendation_stats", {})
    total_shown = sum(float((row or {}).get("shown", 0)) for row in stats.values()) + 1.0
    rescored: List[Dict[str, Any]] = []
    for item in ranked:
        name = str(item.get("name") or "")
        row = stats.get(name, {}) if isinstance(stats, dict) else {}
        shown = float(row.get("shown", 0.0))
        clicked = float(row.get("clicked", 0.0))
        ordered = float(row.get("ordered", 0.0))
        exploitation = (clicked * 0.6 + ordered * 1.4) / max(shown, 1.0)
        exploration = (2.0 * (__import__("math").log(total_shown + 1.0) / max(shown + 1.0, 1.0))) ** 0.5
        item = dict(item)
        ranking = dict(item.get("ranking", {}))
        ranking["exploitation"] = round(exploitation, 4)
        ranking["exploration_bandit"] = round(exploration, 4)
        ranking["score"] = round(float(ranking.get("score", 0.0)) + exploitation * 0.22 + exploration * 0.08, 4)
        item["ranking"] = ranking
        rescored.append(item)
    rescored.sort(key=lambda item: item.get("ranking", {}).get("score", 0.0), reverse=True)
    return rescored[:5]


def get_upsell_suggestion(cart_items: List[Dict[str, Any]], business_id: str, *, phone: str = "") -> Dict[str, Any]:
    exclude = [str(item.get("name", "")) for item in cart_items]
    suggestions = recommend_products(phone, business_id, query="upsell", exclude=exclude)
    return suggestions[0] if suggestions else {}


def personalize_search_results(phone: str, business_id: str, query: str) -> List[Dict[str, Any]]:
    return recommend_products(phone, business_id, query=query)

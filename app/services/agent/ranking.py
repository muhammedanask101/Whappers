from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.services.agent.learning import learned_token_weights, rank_token_match, tokenize


def utc_timestamp(value: Any) -> float:
    if value is None:
        return 0.0
    if hasattr(value, "timestamp"):
        return float(value.timestamp())
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return float(parsed.timestamp())
    except ValueError:
        return 0.0


def recency_decay(value: Any, *, half_life_days: float = 14.0) -> float:
    timestamp = utc_timestamp(value)
    if not timestamp:
        return 0.0
    age_days = max(0.0, (datetime.now(timezone.utc).timestamp() - timestamp) / 86400.0)
    return round(0.5 ** (age_days / max(half_life_days, 0.1)), 4)


def rank_products(
    query: str,
    products: Iterable[Dict[str, Any]],
    *,
    profile: Optional[Dict[str, Any]] = None,
    learning_profile: Optional[Dict[str, Any]] = None,
    include_exploration: bool = True,
) -> List[Dict[str, Any]]:
    profile = profile or {}
    learning_profile = learning_profile or {}
    favorite_products = set(profile.get("favorite_products", []))
    affinity = profile.get("product_affinity", {})
    clicks = learning_profile.get("click_signals", {})
    query_matches = {
        item["value"]: item["score"]
        for item in rank_token_match(
            query,
            (
                " ".join(
                    filter(
                        None,
                        [
                            product.get("display_name", ""),
                            product.get("name", ""),
                            product.get("description", ""),
                            " ".join(product.get("tags", [])),
                        ],
                    )
                )
                for product in products
            ),
            learned_weights=learned_token_weights(learning_profile),
        )
    }

    ranked: List[Dict[str, Any]] = []
    product_list = list(products)
    for product in product_list:
        candidate_text = " ".join(
            filter(
                None,
                [
                    product.get("display_name", ""),
                    product.get("name", ""),
                    product.get("description", ""),
                    " ".join(product.get("tags", [])),
                ],
            )
        )
        semantic_score = query_matches.get(candidate_text, 0.0)
        affinity_score = float(affinity.get(product.get("name", ""), 0.0))
        click_score = float(clicks.get(product.get("name", ""), 0.0))
        favorite_boost = 0.4 if product.get("name") in favorite_products else 0.0
        popularity = float(product.get("popularity_score", 0.0))
        freshness = recency_decay(product.get("updated_at"))
        exploration = 0.08 if include_exploration and click_score == 0 and affinity_score == 0 else 0.0
        total = (
            semantic_score * 0.42
            + affinity_score * 0.18
            + click_score * 0.12
            + favorite_boost
            + popularity * 0.10
            + freshness * 0.08
            + exploration
        )
        enriched = dict(product)
        enriched["ranking"] = {
            "semantic": round(semantic_score, 4),
            "affinity": round(affinity_score, 4),
            "clicks": round(click_score, 4),
            "favorite": favorite_boost,
            "freshness": round(freshness, 4),
            "exploration": round(exploration, 4),
            "score": round(total, 4),
        }
        ranked.append(enriched)

    ranked.sort(key=lambda product: product["ranking"]["score"], reverse=True)
    return ranked


def summarize_affinity(orders: Iterable[Dict[str, Any]]) -> Dict[str, float]:
    counts: Dict[str, float] = {}
    co_counts: Dict[str, float] = {}
    order_count = 0.0

    for order in orders:
        names = [str(item.get("name", "")) for item in order.get("items", []) if item.get("name")]
        if not names:
            continue
        order_count += 1.0
        seen = set(names)
        for name in seen:
            counts[name] = counts.get(name, 0.0) + 1.0
        for left in seen:
            for right in seen:
                if left == right:
                    continue
                key = f"{left}|{right}"
                co_counts[key] = co_counts.get(key, 0.0) + 1.0

    affinity: Dict[str, float] = {}
    if order_count == 0.0:
        return affinity

    for key, value in co_counts.items():
        left, right = key.split("|", 1)
        base = counts.get(left, 1.0)
        affinity[key] = round(value / base, 4)
    return affinity


def product_preference_terms(profile: Dict[str, Any]) -> List[str]:
    terms = set(profile.get("favorite_products", []))
    for category, score in (profile.get("category_affinity") or {}).items():
        if score >= 0.2:
            terms.update(tokenize(category))
    return sorted(terms)

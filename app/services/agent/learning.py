from __future__ import annotations

import math
import re
from collections import Counter
from typing import Any, Dict, Iterable, List, Optional

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now


STOPWORDS = {
    "a", "an", "and", "are", "at", "be", "for", "from", "have", "hello", "hi",
    "i", "if", "in", "is", "it", "me", "my", "of", "on", "or", "please", "the",
    "this", "to", "we", "with", "you", "your",
}


def tokenize(text: str) -> List[str]:
    return [
        token
        for token in re.findall(r"[a-z0-9]+", (text or "").lower())
        if len(token) > 1 and token not in STOPWORDS
    ]


def text_features(text: str) -> Dict[str, Any]:
    tokens = tokenize(text)
    counter = Counter(tokens)
    return {"tokens": tokens, "weights": dict(counter)}


def get_learning_profile(phone: str, business_id: str = DEFAULT_BUSINESS_ID) -> Dict[str, Any]:
    document = get_collection("customer_learning").find_one(
        {"business_id": business_id, "phone": phone}
    )
    if document:
        return document
    return {
        "business_id": business_id,
        "phone": phone,
        "intent_patterns": {},
        "status_patterns": {},
        "product_interest": {},
        "tool_success": {},
        "tool_reward": {},
        "click_signals": {},
        "feedback_signals": {"positive": 0, "negative": 0, "neutral": 0},
        "recommendation_stats": {},
        "policy_state": {"risk": 0.0, "autonomy": 0.5},
        "preference_terms": [],
        "last_intent": "",
        "last_order_signal_at": None,
        "updated_at": utc_now(),
    }


def update_learning_profile(
    phone: str,
    business_id: str,
    *,
    message: str,
    inferred_intent: str,
    tool_name: str = "",
    tool_ok: Optional[bool] = None,
    products: Optional[Iterable[str]] = None,
    vendor_status: str = "",
) -> None:
    features = text_features(message)
    intent_patterns = {
        token: 1
        for token in features["tokens"][:12]
    }
    update: Dict[str, Any] = {"updated_at": utc_now(), "last_intent": inferred_intent}
    inc: Dict[str, Any] = {}

    for token, count in intent_patterns.items():
        inc[f"intent_patterns.{inferred_intent}.{token}"] = count

    if vendor_status:
        for token in features["tokens"][:10]:
            inc[f"status_patterns.{vendor_status}.{token}"] = 1

    for product in products or []:
        inc[f"product_interest.{product}"] = 1
        if tool_name in {"search_catalog", "get_product_details", "recommend_products", "manage_cart"}:
            inc[f"click_signals.{product}"] = 1

    if tool_name:
        inc[f"tool_success.{tool_name}.attempts"] = 1
        if tool_ok:
            inc[f"tool_success.{tool_name}.successes"] = 1
            inc[f"tool_reward.{tool_name}"] = 1
        else:
            inc[f"tool_reward.{tool_name}"] = -1

    if inferred_intent in {"checkout", "cart"}:
        update["last_order_signal_at"] = utc_now()

    get_collection("customer_learning").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$set": update,
            "$inc": inc,
            "$addToSet": {"preference_terms": {"$each": features["tokens"][:10]}},
        },
        upsert=True,
    )


def record_tool_outcome(
    business_id: str,
    *,
    tool_name: str,
    ok: bool,
    reward: float,
) -> None:
    get_collection("tool_policy_stats").update_one(
        {"business_id": business_id, "tool": tool_name},
        {
            "$inc": {
                "attempts": 1,
                "successes": 1 if ok else 0,
                "reward_total": float(reward),
            },
            "$set": {"updated_at": utc_now()},
            "$setOnInsert": {"created_at": utc_now()},
        },
        upsert=True,
    )


def get_tool_policy_stats(business_id: str) -> Dict[str, Dict[str, float]]:
    rows = get_collection("tool_policy_stats").find({"business_id": business_id})
    return {
        str(row.get("tool") or ""): {
            "attempts": float(row.get("attempts", 0.0)),
            "successes": float(row.get("successes", 0.0)),
            "reward_total": float(row.get("reward_total", 0.0)),
        }
        for row in rows
        if row.get("tool")
    }


def record_feedback_signal(phone: str, business_id: str, *, sentiment: str, score: float) -> None:
    sentiment = sentiment if sentiment in {"positive", "negative", "neutral"} else "neutral"
    get_collection("customer_learning").update_one(
        {"business_id": business_id, "phone": phone},
        {
            "$inc": {
                f"feedback_signals.{sentiment}": 1,
                "policy_state.risk": 0.15 if sentiment == "negative" else (-0.08 if sentiment == "positive" else 0.0),
            },
            "$set": {"updated_at": utc_now()},
        },
        upsert=True,
    )
    record_event("feedback_signal", business_id=business_id, phone=phone, payload={"sentiment": sentiment, "score": score})


def record_recommendation_feedback(
    phone: str,
    business_id: str,
    *,
    product_name: str,
    event: str,
) -> None:
    if not product_name:
        return
    increments = {
        "shown": {"shown": 1},
        "clicked": {"clicked": 1},
        "ordered": {"ordered": 1},
    }.get(event, {"shown": 1})
    inc = {f"recommendation_stats.{product_name}.{key}": value for key, value in increments.items()}
    get_collection("customer_learning").update_one(
        {"business_id": business_id, "phone": phone},
        {"$inc": inc, "$set": {"updated_at": utc_now()}},
        upsert=True,
    )
    record_event("recommendation_feedback", business_id=business_id, phone=phone, payload={"product": product_name, "event": event})


def record_event(
    kind: str,
    *,
    business_id: str,
    phone: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    get_collection("interaction_events").insert_one(
        {
            "business_id": business_id,
            "phone": phone,
            "kind": kind,
            "payload": payload or {},
            "created_at": utc_now(),
        }
    )


def recent_events(
    business_id: str,
    *,
    phone: str = "",
    limit: int = 10,
    kinds: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    query: Dict[str, Any] = {"business_id": business_id}
    if phone:
        query["phone"] = phone
    if kinds:
        query["kind"] = {"$in": kinds}
    rows = list(
        get_collection("interaction_events")
        .find(query)
        .sort("created_at", -1)
        .limit(limit)
    )
    rows.reverse()
    return rows


def rank_token_match(query: str, candidates: Iterable[str], learned_weights: Optional[Dict[str, float]] = None) -> List[Dict[str, Any]]:
    query_features = text_features(query)
    query_weights = Counter(query_features["tokens"])
    results: List[Dict[str, Any]] = []

    for candidate in candidates:
        candidate_features = text_features(candidate)
        candidate_weights = Counter(candidate_features["tokens"])
        overlap = set(query_weights) & set(candidate_weights)
        if not overlap:
            score = 0.0
        else:
            numerator = 0.0
            query_norm = 0.0
            candidate_norm = 0.0
            for token, weight in query_weights.items():
                learned = float((learned_weights or {}).get(token, 0.0))
                scaled = weight * (1.0 + learned)
                query_norm += scaled * scaled
                numerator += scaled * candidate_weights.get(token, 0)
            for weight in candidate_weights.values():
                candidate_norm += weight * weight
            score = numerator / (math.sqrt(query_norm or 1.0) * math.sqrt(candidate_norm or 1.0))
        results.append({"value": candidate, "score": round(score, 4)})
    results.sort(key=lambda item: item["score"], reverse=True)
    return results


def learned_token_weights(profile: Dict[str, Any], intent: str = "") -> Dict[str, float]:
    learned = profile.get("intent_patterns", {})
    if intent and isinstance(learned.get(intent), dict):
        total = float(sum(learned[intent].values()) or 1.0)
        return {
            token: round(count / total, 4)
            for token, count in learned[intent].items()
        }
    combined: Counter[str] = Counter()
    for values in learned.values():
        if isinstance(values, dict):
            combined.update(values)
    total = float(sum(combined.values()) or 1.0)
    return {token: round(count / total, 4) for token, count in combined.items()}

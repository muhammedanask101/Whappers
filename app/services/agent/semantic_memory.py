from __future__ import annotations

import math
from collections import Counter
from typing import Any, Dict, Iterable, List

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection, utc_now
from app.services.agent.learning import text_features


def remember(
    phone: str,
    business_id: str,
    *,
    kind: str,
    text: str,
    metadata: Dict[str, Any] | None = None,
) -> None:
    cleaned = str(text or "").strip()
    if not cleaned:
        return
    features = text_features(cleaned)
    get_collection("semantic_memory").insert_one(
        {
            "business_id": business_id or DEFAULT_BUSINESS_ID,
            "phone": phone,
            "kind": kind,
            "text": cleaned[:1200],
            "tokens": features["tokens"][:80],
            "weights": features["weights"],
            "metadata": metadata or {},
            "created_at": utc_now(),
        }
    )


def retrieve(
    phone: str,
    business_id: str,
    *,
    query: str,
    limit: int = 6,
    kinds: Iterable[str] | None = None,
) -> List[Dict[str, Any]]:
    query_features = Counter(text_features(query).get("weights", {}))
    if not query_features:
        return []
    mongo_query: Dict[str, Any] = {"business_id": business_id or DEFAULT_BUSINESS_ID, "phone": phone}
    if kinds:
        mongo_query["kind"] = {"$in": list(kinds)}
    rows = list(
        get_collection("semantic_memory")
        .find(mongo_query)
        .sort("created_at", -1)
        .limit(80)
    )
    scored: List[Dict[str, Any]] = []
    for row in rows:
        score = _cosine(query_features, Counter(row.get("weights") or {}))
        if score <= 0:
            continue
        scored.append(
            {
                "kind": row.get("kind"),
                "text": row.get("text"),
                "metadata": row.get("metadata", {}),
                "created_at": row.get("created_at"),
                "score": round(score, 4),
            }
        )
    scored.sort(key=lambda item: item["score"], reverse=True)
    return scored[:limit]


def summarize_for_prompt(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    return [
        {
            "kind": row.get("kind"),
            "text": str(row.get("text") or "")[:220],
            "score": row.get("score", 0.0),
            "metadata": row.get("metadata", {}),
        }
        for row in rows
    ]


def _cosine(left: Counter[str], right: Counter[str]) -> float:
    overlap = set(left) & set(right)
    if not overlap:
        return 0.0
    numerator = sum(float(left[token]) * float(right[token]) for token in overlap)
    left_norm = math.sqrt(sum(float(weight) * float(weight) for weight in left.values()))
    right_norm = math.sqrt(sum(float(weight) * float(weight) for weight in right.values()))
    return numerator / max(left_norm * right_norm, 1e-9)

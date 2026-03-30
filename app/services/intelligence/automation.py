from __future__ import annotations

import asyncio
import hashlib
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.db.mongo import get_collection, parse_datetime, utc_now
from app.services.agent.learning import record_event
from app.services.agent.metrics import record_metric
from app.services.intelligence.recommendations import recommend_products
from app.services.llm import call_llm
from app.services.vendor import get_business_profile, list_businesses, retry_failed_notifications, send_whatsapp_message_async


AUTOMATION_ACTIONS = {
    "cart_abandoned": "follow-up message",
    "order_delivered": "reorder reminder",
    "customer_inactive": "follow-up message",
    "frequent_buyer": "upsell recommendation",
    "failed_checkout": "support recovery",
}


class AutomationScheduler:
    def __init__(self, *, interval_seconds: int = 120) -> None:
        self.interval_seconds = max(15, int(interval_seconds))
        self._task: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._stopping = asyncio.Event()

    async def start(self) -> None:
        if self._task and not self._task.done():
            return
        self._stopping.clear()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stopping.set()
        if self._task:
            await self._task

    async def run_once(self) -> None:
        async with self._lock:
            await asyncio.to_thread(detect_automation_events)
            await asyncio.to_thread(process_automation_events)
            await asyncio.to_thread(process_automation_feedback)
            await asyncio.to_thread(process_notification_retries)

    async def _run_loop(self) -> None:
        while not self._stopping.is_set():
            try:
                await self.run_once()
            except Exception as exc:
                print("[automation] cycle_failed", repr(exc))
            try:
                await asyncio.wait_for(self._stopping.wait(), timeout=self.interval_seconds)
            except asyncio.TimeoutError:
                continue


def detect_automation_events() -> None:
    for business in list_businesses():
        business_id = str((business or {}).get("business_id") or "")
        if not business_id:
            continue
        _detect_cart_abandoned(business_id)
        _detect_customer_inactive(business_id)
        _detect_frequent_buyer(business_id)
        _detect_order_delivered(business_id)
        _detect_failed_checkout(business_id)


def process_automation_events() -> None:
    now = utc_now()
    rows = list(
        get_collection("automation_events")
        .find({"status": "pending", "scheduled_at": {"$lte": now}})
        .sort("score", -1)
        .limit(50)
    )
    for row in rows:
        business_id = str(row.get("business_id") or "")
        phone = str(row.get("phone") or "")
        trigger = str(row.get("trigger") or "")
        if not business_id or not phone:
            continue
        if _automation_blocked(business_id, phone):
            get_collection("automation_events").update_one({"_id": row["_id"]}, {"$set": {"status": "suppressed", "updated_at": utc_now()}})
            continue
        message = _generate_automation_message(row)
        if not message:
            get_collection("automation_events").update_one({"_id": row["_id"]}, {"$set": {"status": "failed", "updated_at": utc_now(), "last_error": "generation_failed"}})
            continue
        profile = get_business_profile(business_id)
        result = asyncio.run(
            send_whatsapp_message_async(
                phone,
                message,
                from_number=profile.get("whatsapp_number"),
                business_id=business_id,
                notification_type=f"automation_{trigger}",
                metadata={"automation_key": str(row.get("event_key") or ""), "trigger": trigger},
            )
        )
        status = "sent" if result.get("success") else ("template_required" if result.get("template_required") else "failed")
        get_collection("automation_events").update_one(
            {"_id": row["_id"]},
            {
                "$set": {
                    "status": status,
                    "message": message,
                    "sent_result": result,
                    "updated_at": utc_now(),
                }
            },
        )
        get_collection("automation_log").insert_one(
            {
                "business_id": business_id,
                "phone": phone,
                "message": message,
                "kind": trigger,
                "created_at": utc_now(),
            }
        )
        record_event(trigger, business_id=business_id, phone=phone, payload={"message": message, "score": row.get("score", 0.0)})
        record_metric(business_id, metric="automation_sent", dimensions={"kind": trigger, "phone": phone, "status": status})


def process_automation_feedback() -> None:
    one_day_ago = utc_now() - timedelta(hours=24)
    rows = list(get_collection("automation_events").find({"status": "sent", "updated_at": {"$gte": one_day_ago}}))
    for row in rows:
        business_id = str(row.get("business_id") or "")
        phone = str(row.get("phone") or "")
        trigger = str(row.get("trigger") or "")
        if not business_id:
            continue
        converted = get_collection("orders").count_documents({"business_id": business_id, "customer_phone": phone, "created_at": {"$gte": row.get("updated_at")}}, limit=1) > 0
        get_collection("automation_runs").update_one(
            {"business_id": business_id, "source_event_id": row.get("_id")},
            {
                "$set": {
                    "business_id": business_id,
                    "phone": phone,
                    "kind": trigger,
                    "converted": converted,
                    "updated_at": utc_now(),
                },
                "$setOnInsert": {"created_at": utc_now(), "payload": row.get("payload", {}), "score": row.get("score", 0.0)},
            },
            upsert=True,
        )
        record_metric(business_id, metric="automation_conversion", amount=1.0 if converted else 0.0, dimensions={"kind": trigger, "phone": phone})


def process_notification_retries() -> None:
    for business in list_businesses():
        business_id = str((business or {}).get("business_id") or "")
        if not business_id:
            continue
        retried = retry_failed_notifications(business_id)
        if retried:
            record_metric(business_id, metric="automation_retry_success", amount=float(retried), dimensions={"kind": "vendor_notification"})


def _detect_cart_abandoned(business_id: str) -> None:
    cutoff = utc_now() - timedelta(hours=2)
    rows = list(
        get_collection("conversation_state").find(
            {
                "business_id": business_id,
                "role": "customer",
                "cart.items.0": {"$exists": True},
                "updated_at": {"$lte": cutoff},
            }
        )
    )
    for row in rows:
        phone = str(row.get("phone") or "")
        has_order = get_collection("orders").count_documents({"business_id": business_id, "customer_phone": phone, "updated_at": {"$gte": cutoff}}, limit=1) > 0
        if has_order:
            continue
        score = min(1.0, 0.4 + 0.15 * len((row.get("cart") or {}).get("items", [])))
        _queue_automation_event(business_id, phone, "cart_abandoned", {"cart": row.get("cart", {})}, score=score)


def _detect_customer_inactive(business_id: str) -> None:
    cutoff = utc_now() - timedelta(days=5)
    rows = list(get_collection("customer_profiles").find({"business_id": business_id, "last_active_at": {"$lte": cutoff}}))
    for row in rows:
        score = 0.45 + min(float((row.get("rfm") or {}).get("frequency_score", 0.0)), 1.0) * 0.25
        _queue_automation_event(business_id, str(row.get("phone") or ""), "customer_inactive", {"segment": row.get("segment", "unknown")}, score=score)


def _detect_frequent_buyer(business_id: str) -> None:
    rows = list(get_collection("customer_profiles").find({"business_id": business_id}))
    for row in rows:
        orders_count = int((row.get("rfm") or {}).get("order_count", 0))
        if orders_count < 3:
            continue
        score = min(1.0, 0.55 + orders_count * 0.05)
        _queue_automation_event(business_id, str(row.get("phone") or ""), "frequent_buyer", {"order_count": orders_count}, score=score)


def _detect_order_delivered(business_id: str) -> None:
    cutoff = utc_now() - timedelta(days=3)
    rows = list(get_collection("orders").find({"business_id": business_id, "status": "delivered", "updated_at": {"$gte": cutoff}}))
    for row in rows:
        score = min(1.0, 0.5 + float(row.get("total", 0.0)) / 2000.0)
        _queue_automation_event(business_id, str(row.get("customer_phone") or ""), "order_delivered", {"order_id": row.get("order_id"), "total": row.get("total", 0.0)}, score=score)


def _detect_failed_checkout(business_id: str) -> None:
    cutoff = utc_now() - timedelta(hours=12)
    rows = list(
        get_collection("interaction_events").find(
            {
                "business_id": business_id,
                "kind": "incoming_message",
                "created_at": {"$gte": cutoff},
            }
        )
    )
    for row in rows:
        payload = row.get("payload") or {}
        message = str(payload.get("message") or "").lower()
        if "payment failed" not in message and "checkout failed" not in message:
            continue
        _queue_automation_event(business_id, str(row.get("phone") or ""), "failed_checkout", {"message": payload.get("message", "")}, score=0.78)


def _queue_automation_event(business_id: str, phone: str, trigger: str, payload: Dict[str, Any], *, score: float) -> None:
    normalized_phone = str(phone or "").strip()
    if not business_id or not normalized_phone:
        return
    payload_key = hashlib.sha256(str(sorted(payload.items())).encode("utf-8")).hexdigest()[:16]
    event_key = f"{business_id}:{trigger}:{normalized_phone}:{payload_key}"
    get_collection("automation_events").update_one(
        {"event_key": event_key},
        {
            "$setOnInsert": {
                "business_id": business_id,
                "phone": normalized_phone,
                "trigger": trigger,
                "action": AUTOMATION_ACTIONS.get(trigger, "follow-up message"),
                "payload": payload,
                "score": round(max(0.0, min(score, 1.0)), 3),
                "scheduled_at": utc_now(),
                "status": "pending",
                "created_at": utc_now(),
                "updated_at": utc_now(),
                "event_key": event_key,
            }
        },
        upsert=True,
    )


def _generate_automation_message(event: Dict[str, Any]) -> str:
    business_id = str(event.get("business_id") or "")
    phone = str(event.get("phone") or "")
    trigger = str(event.get("trigger") or "")
    payload = dict(event.get("payload") or {})
    recommendations = recommend_products(phone, business_id, query=trigger)
    top_products = [row.get("display_name") for row in recommendations[:3] if row.get("display_name")]
    profile = get_business_profile(business_id)
    prompt_payload = {
        "business_name": profile.get("name", "the business"),
        "trigger": trigger,
        "action": event.get("action"),
        "payload": payload,
        "recommended_products": top_products,
        "instructions": "Write a concise WhatsApp commerce message. Be helpful, commercial, and natural. Do not mention internal systems. Do not fabricate discounts.",
    }
    try:
        message = call_llm(
            [
                {"role": "system", "content": "You generate outbound WhatsApp automation messages for commerce businesses. Keep it under 320 characters and tailored to the trigger and customer context."},
                {"role": "user", "content": str(prompt_payload)},
            ],
            tier="balanced",
        ).strip()
    except Exception:
        message = ""
    return message[:320]


def _automation_blocked(business_id: str, phone: str) -> bool:
    profile = get_collection("businesses").find_one({"business_id": business_id}) or {}
    cooldown_minutes = int(profile.get("automation_cooldown_minutes") or 180)
    max_messages = int(profile.get("automation_max_messages_per_day") or 2)
    cutoff = utc_now() - timedelta(minutes=max(1, cooldown_minutes))
    daily_cutoff = utc_now() - timedelta(hours=24)
    recent = get_collection("automation_log").count_documents({"business_id": business_id, "phone": phone, "created_at": {"$gte": cutoff}})
    daily = get_collection("automation_log").count_documents({"business_id": business_id, "phone": phone, "created_at": {"$gte": daily_cutoff}})
    return recent > 0 or daily >= max_messages

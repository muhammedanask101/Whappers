from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from typing import Any, Dict

from app.db.mongo import get_collection, parse_datetime, utc_now
from app.services.agent.learning import record_event
from app.services.agent.metrics import record_metric
from app.services.agent.localization import contact_style, style_text
from app.services.intelligence.recommendations import recommend_products
from app.services.vendor import get_business_profile, send_whatsapp_message_async


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
            await asyncio.to_thread(process_customers)
            await asyncio.to_thread(process_orders)
            await asyncio.to_thread(process_support_recovery)
            await asyncio.to_thread(process_campaign_feedback)

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


def process_customers() -> None:
    for customer in get_collection("customer_profiles").find({}, {"phone": 1, "business_id": 1, "last_active_at": 1, "rfm": 1, "segment": 1}):
        phone = str(customer.get("phone") or "")
        business_id = str(customer.get("business_id") or "")
        if not phone or not business_id or _blocked_by_spam_guard(business_id, phone):
            continue
        last_active = _as_datetime(customer.get("last_active_at"))
        if not last_active or datetime.now(timezone.utc) - last_active < _customer_inactivity_threshold(customer):
            continue
        recommendations = recommend_products(phone, business_id, query="reactivation")
        top = recommendations[0]["display_name"] if recommendations else "our latest items"
        style = contact_style(phone, business_id)
        _safe_send(
            business_id,
            phone,
            style_text(
                style,
                f"It has been a while. Want to see fresh picks like {top}?",
                f"Kurachu naalayi. {top} pole puthiya picks kaanano?",
                f"Kurachu naalayi. {top} pole puthiya picks kaanano?",
            ),
            event_kind="customer_reactivation",
        )


def process_orders() -> None:
    now = datetime.now(timezone.utc)
    for order in get_collection("orders").find({}, {"business_id": 1, "customer_phone": 1, "status": 1, "updated_at": 1, "created_at": 1, "order_id": 1, "total": 1}):
        business_id = str(order.get("business_id") or "")
        customer_phone = str(order.get("customer_phone") or "")
        if not business_id or not customer_phone or _blocked_by_spam_guard(business_id, customer_phone):
            continue
        status = str(order.get("status") or "")
        updated_at = _as_datetime(order.get("updated_at")) or _as_datetime(order.get("created_at"))
        if not updated_at:
            continue
        minutes_open = (now - updated_at).total_seconds() / 60.0
        threshold = _order_threshold(status)
        if minutes_open < threshold:
            continue
        if status == "pending":
            vendor_phone = str(get_business_profile(business_id).get("vendor_phone") or "")
            if vendor_phone and not _blocked_by_spam_guard(business_id, vendor_phone):
                style = contact_style(vendor_phone, business_id)
                _safe_send(
                    business_id,
                    vendor_phone,
                    style_text(
                        style,
                        f"Order {order.get('order_id')} is still pending. Please update it.",
                        f"Order {order.get('order_id')} ippolum pending aanu. Dayavaayi update cheyyu.",
                        f"Order {order.get('order_id')} ippolum pending aanu. Dayavaayi update cheyyu.",
                    ),
                    event_kind="vendor_order_nudge",
                )
        elif status in {"accepted", "out_for_delivery"}:
            style = contact_style(customer_phone, business_id)
            status_text = "being prepared" if status == "accepted" else "on the way"
            _safe_send(
                business_id,
                customer_phone,
                style_text(
                    style,
                    f"Your order {order.get('order_id')} is {status_text}.",
                    f"Ningalude order {order.get('order_id')} {status_text} aanu.",
                    f"Ningalude order {order.get('order_id')} {status_text} aanu.",
                ),
                event_kind="customer_order_followup",
            )


def process_support_recovery() -> None:
    cutoff = utc_now() - timedelta(hours=6)
    for ticket in get_collection("support_tickets").find({"status": {"$ne": "resolved"}, "created_at": {"$lte": cutoff}}, {"ticket_id": 1, "customer_phone": 1, "business_id": 1}):
        phone = str(ticket.get("customer_phone") or "")
        business_id = str(ticket.get("business_id") or "")
        if not phone or not business_id or _blocked_by_spam_guard(business_id, phone):
            continue
        style = contact_style(phone, business_id)
        _safe_send(
            business_id,
            phone,
            style_text(
                style,
                f"We are still working on ticket {ticket.get('ticket_id')}. Reply here if anything changed.",
                f"Ticket {ticket.get('ticket_id')} ippolum handle cheyyunnu. Enthengilum maattam undengil ivide reply cheyyu.",
                f"Ticket {ticket.get('ticket_id')} ippolum handle cheyyunnu. Enthengilum maattam undengil ivide reply cheyyu.",
            ),
            event_kind="support_recovery_followup",
        )


def process_campaign_feedback() -> None:
    one_day_ago = utc_now() - timedelta(hours=24)
    rows = list(get_collection("interaction_events").find({"kind": {"$in": ["customer_reactivation", "broadcast_sent"]}, "created_at": {"$gte": one_day_ago}}))
    for row in rows:
        business_id = str(row.get("business_id") or "")
        phone = str(row.get("phone") or "")
        if not business_id:
            continue
        converted = False
        if phone:
            converted = get_collection("orders").count_documents({"business_id": business_id, "customer_phone": phone, "created_at": {"$gte": row["created_at"]}}, limit=1) > 0
        get_collection("automation_runs").update_one(
            {"business_id": business_id, "source_event_id": row.get("_id")},
            {
                "$set": {"business_id": business_id, "phone": phone, "kind": row.get("kind"), "converted": converted, "updated_at": utc_now()},
                "$setOnInsert": {"created_at": utc_now(), "payload": row.get("payload", {})},
            },
            upsert=True,
        )
        record_metric(business_id, metric="automation_conversion", amount=1.0 if converted else 0.0, dimensions={"kind": row.get("kind"), "phone": phone})


def _safe_send(business_id: str, phone: str, message: str, *, event_kind: str) -> None:
    profile = get_business_profile(business_id)
    result = asyncio.run(send_whatsapp_message_async(phone, message, from_number=profile.get("whatsapp_number"), business_id=business_id, notification_type=event_kind))
    if not result.get("success"):
        return
    get_collection("automation_log").insert_one({"business_id": business_id, "phone": phone, "message": message, "kind": event_kind, "created_at": utc_now()})
    record_event(event_kind, business_id=business_id, phone=phone, payload={"message": message})
    record_metric(business_id, metric="automation_sent", dimensions={"kind": event_kind, "phone": phone})


def _blocked_by_spam_guard(business_id: str, phone: str) -> bool:
    day_ago = utc_now() - timedelta(hours=24)
    recent = list(get_collection("automation_log").find({"business_id": business_id, "phone": phone, "created_at": {"$gte": day_ago}}).sort("created_at", -1).limit(4))
    if len(recent) >= 3:
        return True
    if recent:
        latest = _as_datetime(recent[0].get("created_at"))
        if latest and datetime.now(timezone.utc) - latest < timedelta(minutes=30):
            return True
    return False


def _customer_inactivity_threshold(customer: Dict[str, Any]) -> timedelta:
    if customer.get("segment") == "vip":
        return timedelta(days=5)
    return timedelta(days=3)


def _order_threshold(status: str) -> float:
    return {"pending": 8.0, "accepted": 25.0, "out_for_delivery": 18.0}.get(status, 20.0) * 1.35


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return parse_datetime(value)

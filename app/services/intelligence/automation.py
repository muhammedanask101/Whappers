from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List

from app.db.mongo import get_collection, utc_now
from app.services.agent.learning import record_event
from app.services.agent.metrics import record_metric
from app.services.agent.localization import contact_style, style_text
from app.services.intelligence.customer_profile import get_customer_profile
from app.services.intelligence.recommendations import recommend_products
from app.services.vendor import get_business_profile, send_whatsapp_message


FOLLOWUP_INTERVAL = int(float(__import__("os").getenv("AUTOMATION_INTERVAL_SECONDS", "120")))


def run_automation_cycle() -> None:
    while True:
        try:
            process_customers()
            process_orders()
            process_support_recovery()
            process_campaign_feedback()
        except Exception as exc:
            print("Automation error:", exc)
        time.sleep(FOLLOWUP_INTERVAL)


def process_customers() -> None:
    for customer in get_collection("customer_profiles").find():
        phone = str(customer.get("phone") or "")
        business_id = str(customer.get("business_id") or "")
        if not phone or not business_id:
            continue
        if _blocked_by_spam_guard(business_id, phone):
            continue

        last_active = _as_datetime(customer.get("last_active_at"))
        if not last_active:
            continue

        inactivity_threshold = _customer_inactivity_threshold(customer)
        if datetime.now(timezone.utc) - last_active < inactivity_threshold:
            continue
        score = _customer_reactivation_score(customer, business_id, phone)
        if score < 0.52:
            continue

        recommendations = recommend_products(phone, business_id, query="reactivation")
        top = recommendations[0]["display_name"] if recommendations else "our latest items"
        style = contact_style(phone, business_id)
        message = style_text(
            style,
            f"It has been a while. Want to see fresh picks like {top}?",
            f"Kurachu naalayi. {top} pole puthiya picks kaanano?",
            f"കുറച്ച് നാളായി. {top} പോലെയുള്ള പുതിയ തെരഞ്ഞെടുപ്പുകൾ കാണണോ?",
        )
        _safe_send(business_id, phone, message, event_kind="customer_reactivation")


def process_orders() -> None:
    now = datetime.now(timezone.utc)
    for order in get_collection("orders").find():
        business_id = str(order.get("business_id") or "")
        customer_phone = str(order.get("customer_phone") or "")
        if not business_id or not customer_phone:
            continue
        if _blocked_by_spam_guard(business_id, customer_phone):
            continue

        status = str(order.get("status") or "")
        updated_at = _as_datetime(order.get("updated_at")) or _as_datetime(order.get("created_at"))
        if not updated_at:
            continue
        minutes_open = (now - updated_at).total_seconds() / 60.0
        threshold = _order_threshold(order, status)
        if minutes_open < threshold:
            continue
        if _order_escalation_score(order, status, minutes_open) < 0.5:
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
                        f"Order {order.get('order_id')} ഇപ്പോഴും pending ആണ്. ദയവായി update ചെയ്യൂ.",
                    ),
                    event_kind="vendor_order_nudge",
                )
        elif status == "accepted":
            style = contact_style(customer_phone, business_id)
            _safe_send(
                business_id,
                customer_phone,
                style_text(
                    style,
                    f"Your order {order.get('order_id')} is being prepared. We are tracking it for you.",
                    f"Ningalude order {order.get('order_id')} ready aakkukayaanu. Njangal track cheyyunnu.",
                    f"നിങ്ങളുടെ order {order.get('order_id')} തയ്യാറാക്കുകയാണ്. ഞങ്ങൾ അത് ട്രാക്ക് ചെയ്യുന്നു.",
                ),
                event_kind="customer_delay_followup",
            )
        elif status == "out_for_delivery":
            style = contact_style(customer_phone, business_id)
            _safe_send(
                business_id,
                customer_phone,
                style_text(
                    style,
                    f"Your order {order.get('order_id')} is on the way.",
                    f"Ningalude order {order.get('order_id')} vazhiyil aanu.",
                    f"നിങ്ങളുടെ order {order.get('order_id')} വഴിയിലാണ്.",
                ),
                event_kind="customer_delivery_followup",
            )


def process_support_recovery() -> None:
    cutoff = utc_now() - timedelta(hours=6)
    tickets = get_collection("support_tickets").find({"status": {"$ne": "resolved"}, "created_at": {"$lte": cutoff}})
    for ticket in tickets:
        phone = str(ticket.get("customer_phone") or "")
        business_id = str(ticket.get("business_id") or "")
        if not phone or not business_id or _blocked_by_spam_guard(business_id, phone):
            continue
        if _support_recovery_score(ticket) < 0.45:
            continue
        style = contact_style(phone, business_id)
        _safe_send(
            business_id,
            phone,
            style_text(
                style,
                f"We are still working on ticket {ticket.get('ticket_id')}. If anything changed, reply here and I will update it.",
                f"Ticket {ticket.get('ticket_id')} ippolum handle cheyyunnu. Enthengilum maattam undengil ivide reply cheyyu.",
                f"Ticket {ticket.get('ticket_id')} ഇപ്പോഴും കൈകാര്യം ചെയ്യുകയാണ്. എന്തെങ്കിലും മാറ്റമുണ്ടെങ്കിൽ ഇവിടെ reply ചെയ്യൂ.",
            ),
            event_kind="support_recovery_followup",
        )


def process_campaign_feedback() -> None:
    one_day_ago = utc_now() - timedelta(hours=24)
    rows = list(
        get_collection("interaction_events").find(
            {"kind": {"$in": ["customer_reactivation", "broadcast_sent"]}, "created_at": {"$gte": one_day_ago}}
        )
    )
    for row in rows:
        business_id = str(row.get("business_id") or "")
        phone = str(row.get("phone") or "")
        payload = row.get("payload", {})
        if not business_id:
            continue
        converted = False
        if phone:
            converted = get_collection("orders").count_documents(
                {
                    "business_id": business_id,
                    "customer_phone": phone,
                    "created_at": {"$gte": row["created_at"]},
                },
                limit=1,
            ) > 0
        get_collection("automation_runs").update_one(
            {"business_id": business_id, "source_event_id": row.get("_id")},
            {
                "$set": {
                    "business_id": business_id,
                    "phone": phone,
                    "kind": row.get("kind"),
                    "converted": converted,
                    "payload": payload,
                    "updated_at": utc_now(),
                },
                "$setOnInsert": {"created_at": utc_now()},
            },
            upsert=True,
        )
        record_metric(business_id, metric="automation_conversion", amount=1.0 if converted else 0.0, dimensions={"kind": row.get("kind"), "phone": phone})


def _customer_inactivity_threshold(customer: Dict[str, Any]) -> timedelta:
    rfm = customer.get("rfm", {})
    recency = float(rfm.get("recency", 0.0))
    if customer.get("segment") == "vip":
        return timedelta(days=5)
    if recency >= 0.45:
        return timedelta(days=4)
    return timedelta(days=2)


def _order_threshold(order: Dict[str, Any], status: str) -> float:
    history = list(
        get_collection("orders").find({"business_id": order.get("business_id"), "status": status}).sort("updated_at", -1).limit(40)
    )
    durations = []
    for item in history:
        created = _as_datetime(item.get("created_at"))
        updated = _as_datetime(item.get("updated_at"))
        if created and updated:
            durations.append((updated - created).total_seconds() / 60.0)
    if durations:
        durations.sort()
        baseline = durations[len(durations) // 2]
    else:
        baseline = {"pending": 8.0, "accepted": 25.0, "out_for_delivery": 18.0}.get(status, 20.0)
    return baseline * 1.35


def _safe_send(business_id: str, phone: str, message: str, *, event_kind: str) -> None:
    profile = get_business_profile(business_id)
    send_whatsapp_message(phone, message, from_number=profile.get("whatsapp_number"))
    get_collection("automation_log").insert_one(
        {
            "business_id": business_id,
            "phone": phone,
            "message": message,
            "kind": event_kind,
            "created_at": utc_now(),
        }
    )
    record_event(event_kind, business_id=business_id, phone=phone, payload={"message": message})
    record_metric(business_id, metric="automation_sent", dimensions={"kind": event_kind, "phone": phone})


def _blocked_by_spam_guard(business_id: str, phone: str) -> bool:
    day_ago = utc_now() - timedelta(hours=24)
    recent = list(
        get_collection("automation_log")
        .find({"business_id": business_id, "phone": phone, "created_at": {"$gte": day_ago}})
        .sort("created_at", -1)
        .limit(4)
    )
    if len(recent) >= 3:
        return True
    if recent:
        latest = _as_datetime(recent[0].get("created_at"))
        if latest and datetime.now(timezone.utc) - latest < timedelta(minutes=30):
            return True
    return False


def _as_datetime(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    return None


def _customer_reactivation_score(customer: Dict[str, Any], business_id: str, phone: str) -> float:
    rfm = customer.get("rfm", {})
    recency = 1.0 - float(rfm.get("recency", 0.0))
    monetary = float(rfm.get("monetary", 0.0))
    feedback = get_collection("customer_learning").find_one({"business_id": business_id, "phone": phone}, {"feedback_signals": 1}) or {}
    feedback_signals = feedback.get("feedback_signals", {})
    negative = float(feedback_signals.get("negative", 0.0))
    positive = float(feedback_signals.get("positive", 0.0))
    return round(min(1.0, recency * 0.45 + monetary * 0.2 + positive * 0.05 - negative * 0.08 + 0.3), 4)


def _order_escalation_score(order: Dict[str, Any], status: str, minutes_open: float) -> float:
    baseline = _order_threshold(order, status)
    lateness = max(0.0, minutes_open - baseline) / max(baseline, 1.0)
    value = min(1.0, float(order.get("total", 0.0)) / 2000.0)
    return round(min(1.0, lateness * 0.55 + value * 0.18 + 0.28), 4)


def _support_recovery_score(ticket: Dict[str, Any]) -> float:
    age_hours = max(0.0, (utc_now() - ticket.get("created_at")).total_seconds() / 3600.0) if isinstance(ticket.get("created_at"), datetime) else 6.0
    priority = str(ticket.get("priority") or "normal")
    priority_boost = {"high": 0.28, "urgent": 0.4}.get(priority, 0.12)
    return round(min(1.0, min(age_hours / 24.0, 1.0) * 0.52 + priority_boost + 0.2), 4)

import time
from datetime import datetime, timedelta

from app.db.mongo import get_collection
from app.services.intelligence.events import trigger_event
from app.services.vendor import send_whatsapp_message


FOLLOWUP_INTERVAL = 60  # seconds (increase later)


def run_automation_cycle():
    """
    Continuous background automation loop
    """
    while True:
        try:
            process_customers()
            process_orders()
        except Exception as e:
            print("Automation error:", e)

        time.sleep(FOLLOWUP_INTERVAL)


# -------------------------
# 🧑‍💼 CUSTOMER AUTOMATION
# -------------------------
def process_customers():
    customers = get_collection("customer_profiles").find()
    now = datetime.utcnow()

    for customer in customers:
        last_active = customer.get("last_active_at")

        if not last_active:
            continue

        # 🔥 Inactive user trigger
        if now - last_active > timedelta(days=2):
            trigger_event("user_inactive", {
                "phone": customer["phone"],
                "business_id": customer["business_id"]
            })


# -------------------------
# 📦 ORDER AUTOMATION (NEW)
# -------------------------
def process_orders():
    orders = get_collection("orders").find()
    now = datetime.utcnow()

    for order in orders:
        created_at = order.get("created_at")
        updated_at = order.get("updated_at") or created_at

        if not created_at:
            continue

        elapsed = now - updated_at

        status = order.get("status")
        customer = order.get("customer_phone")
        business_id = order.get("business_id")

        # Prevent spam
        last_notified = order.get("last_followup_sent_at")

        def should_send(cooldown_minutes=10):
            if not last_notified:
                return True
            return (now - last_notified) > timedelta(minutes=cooldown_minutes)

        # -------------------------
        # ⏱ DELAY DETECTION
        # -------------------------
        if status == "accepted" and elapsed > timedelta(minutes=20) and should_send():
            send_whatsapp_message(
                customer,
                "Sorry for the delay 🙏 Your order is taking longer than expected. We are checking with the vendor.",
            )
            mark_notified(order)

        # -------------------------
        # 🚚 DELIVERY FOLLOW-UP
        # -------------------------
        elif status == "out_for_delivery" and elapsed > timedelta(minutes=10) and should_send():
            send_whatsapp_message(
                customer,
                "Your order is on the way 🚚 It should reach you soon!",
            )
            mark_notified(order)

        # -------------------------
        # 🔔 VENDOR NUDGE
        # -------------------------
        elif status == "pending" and elapsed > timedelta(minutes=5) and should_send():
            vendor_phone = get_vendor_phone(business_id)

            if vendor_phone:
                send_whatsapp_message(
                    vendor_phone,
                    "⚠️ You have a pending order. Please accept or reject it.",
                )
                mark_notified(order)


# -------------------------
# HELPERS
# -------------------------
def mark_notified(order):
    get_collection("orders").update_one(
        {"_id": order["_id"]},
        {"$set": {"last_followup_sent_at": datetime.utcnow()}}
    )


def get_vendor_phone(business_id: str):
    business = get_collection("businesses").find_one({"business_id": business_id})
    if not business:
        return None
    return business.get("vendor_phone")
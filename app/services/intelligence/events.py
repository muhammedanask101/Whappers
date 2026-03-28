from datetime import datetime, timedelta

from app.db.mongo import get_collection
from app.services.vendor import send_whatsapp_message


def trigger_event(event_type: str, data: dict):
    if event_type == "order_created":
        handle_order_created(data)

    if event_type == "user_inactive":
        handle_user_inactive(data)


# -------------------------
# EVENT HANDLERS
# -------------------------

def handle_order_created(data: dict):
    phone = data["phone"]
    business_id = data["business_id"]

    # Follow-up message after order
    message = "Thanks for your order! 😊 We’ll update you soon. Let me know if you need anything else."

    send_whatsapp_message(phone, message)


def handle_user_inactive(data: dict):
    phone = data["phone"]
    business_id = data["business_id"]

    message = "Hey! It’s been a while 👀\nWe have some great offers waiting for you. Want to see?"

    send_whatsapp_message(phone, message)
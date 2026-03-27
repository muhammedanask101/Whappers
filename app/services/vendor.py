import base64
import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from app.db.database import execute, fetch_all, fetch_one
from app.models.order import OrderRecord


def normalize_phone(phone: str) -> str:
    phone = phone.strip()
    if phone.startswith("whatsapp:"):
        return phone
    if phone.startswith("+"):
        return f"whatsapp:{phone}"
    digits = re.sub(r"[^\d+]", "", phone)
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return f"whatsapp:{digits}"


def send_whatsapp_message(to: str, body: str) -> Dict[str, Any]:
    account_sid = os.getenv("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = os.getenv("TWILIO_AUTH_TOKEN", "").strip()
    from_number = os.getenv("TWILIO_WHATSAPP_NUMBER", "").strip()
    if not all([account_sid, auth_token, from_number]):
        raise RuntimeError("Twilio WhatsApp environment variables are not fully configured.")

    payload = urllib.parse.urlencode(
        {
            "From": normalize_phone(from_number),
            "To": normalize_phone(to),
            "Body": body,
        }
    ).encode("utf-8")
    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(
        f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json",
        data=payload,
        headers={
            "Authorization": f"Basic {credentials}",
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"Twilio message send failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"Twilio message send failed: {exc.reason}") from exc


def ensure_default_products() -> None:
    row = fetch_one("SELECT COUNT(1) AS count FROM products")
    if row and row["count"] > 0:
        return

    seed_products = [
        ("coffee", 80.0, "cup"),
        ("tea", 20.0, "cup"),
        ("sandwich", 120.0, "piece"),
        ("banana chips", 140.0, "pack"),
        ("milk", 35.0, "packet"),
    ]
    now = datetime.now(timezone.utc).isoformat()
    for name, price, unit in seed_products:
        execute(
            """
            INSERT INTO products (name, price, unit, active_offer_price, offer_until, updated_at)
            VALUES (?, ?, ?, NULL, NULL, ?)
            """,
            (name, price, unit, now),
        )


def get_product_catalog() -> List[Dict[str, Any]]:
    ensure_default_products()
    rows = fetch_all("SELECT name, price, unit, active_offer_price, offer_until FROM products ORDER BY name ASC")
    return [
        {
            "name": row["name"],
            "price": row["price"],
            "unit": row["unit"],
            "offer_price": row["active_offer_price"],
            "offer_until": row["offer_until"],
        }
        for row in rows
    ]


def get_product_catalog_map() -> Dict[str, Dict[str, Any]]:
    return {product["name"].lower(): product for product in get_product_catalog()}


def match_products(query: str) -> List[Dict[str, Any]]:
    catalog = get_product_catalog()
    lowered = query.lower()
    matched = [product for product in catalog if product["name"].lower() in lowered]
    return matched or catalog


def format_catalog(style: str, products: Optional[List[Dict[str, Any]]] = None) -> str:
    product_list = products or get_product_catalog()
    lines = []
    for product in product_list:
        effective_price = product["offer_price"] if product["offer_price"] is not None else product["price"]
        line = f"- {product['name']}: Rs {effective_price:.2f}/{product['unit']}"
        if product["offer_price"] is not None and product["offer_until"]:
            line += f" (offer until {product['offer_until']})"
        lines.append(line)

    if style == "malayalam":
        intro = "ഇപ്പോൾ ലഭ്യമായ products:"
    elif style == "manglish":
        intro = "Ippo available items:"
    else:
        intro = "Currently available products:"
    return intro + "\n" + "\n".join(lines)


def send_vendor_order_notification(order: OrderRecord) -> None:
    vendor_phone = os.getenv("VENDOR_PHONE", "").strip()
    if not vendor_phone:
        raise RuntimeError("VENDOR_PHONE is not configured.")

    message = (
        "New Order:\n"
        f"Order ID: {order.orderId}\n"
        f"Items: {', '.join(f'{item.name} x {item.qty}' for item in order.items)}\n"
        f"Address: {order.address}\n"
        f"Customer: {order.customerPhone}\n"
        f"Total: Rs {order.total:.2f}\n"
        "Reply in natural language like 'ok', 'reject cheyyu', or 'dispatch cheythu'."
    )
    send_whatsapp_message(vendor_phone, message)


def update_product_offer(product: str, offer_price: float, offer_until: Optional[str]) -> Dict[str, Any]:
    ensure_default_products()
    now = datetime.now(timezone.utc).isoformat()
    existing = fetch_one("SELECT price, unit FROM products WHERE name = ?", (product.lower(),))
    base_price = existing["price"] if existing else offer_price
    unit = existing["unit"] if existing else "unit"
    execute(
        """
        INSERT INTO products (name, price, unit, active_offer_price, offer_until, updated_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET
            active_offer_price = excluded.active_offer_price,
            offer_until = excluded.offer_until,
            updated_at = excluded.updated_at
        """,
        (product.lower(), base_price, unit, offer_price, offer_until, now),
    )
    row = fetch_one(
        "SELECT name, price, active_offer_price, offer_until FROM products WHERE name = ?",
        (product.lower(),),
    )
    return {
        "product": row["name"],
        "price": row["price"],
        "offer_price": row["active_offer_price"],
        "offer_until": row["offer_until"],
    }


def update_product_price(product: str, price: float) -> Dict[str, Any]:
    ensure_default_products()
    now = datetime.now(timezone.utc).isoformat()
    current = fetch_one("SELECT unit FROM products WHERE name = ?", (product.lower(),))
    unit = current["unit"] if current else "unit"
    execute(
        """
        INSERT INTO products (name, price, unit, active_offer_price, offer_until, updated_at)
        VALUES (?, ?, ?, NULL, NULL, ?)
        ON CONFLICT(name) DO UPDATE SET
            price = excluded.price,
            updated_at = excluded.updated_at
        """,
        (product.lower(), price, unit, now),
    )
    row = fetch_one("SELECT name, price FROM products WHERE name = ?", (product.lower(),))
    return {"product": row["name"], "price": row["price"]}


def format_offer_preview(action: Dict[str, Any], style: str) -> str:
    date = action.get("date") or "open-ended"
    if style == "malayalam":
        return (
            f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
            f"Until: {date}\nYES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
        )
    if style == "manglish":
        return (
            f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
            f"Until: {date}\nYES ayachal execute, NO ayachal cancel."
        )
    return (
        f"Offer preview:\nProduct: {action['product']}\nOffer price: Rs {float(action['price']):.2f}\n"
        f"Until: {date}\nReply YES to execute or NO to cancel."
    )


def format_price_update_preview(action: Dict[str, Any], style: str) -> str:
    if style == "malayalam":
        return (
            f"Price update preview:\nProduct: {action['product']}\nNew price: Rs {float(action['price']):.2f}\n"
            "YES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
        )
    if style == "manglish":
        return (
            f"Price update preview:\nProduct: {action['product']}\nNew price: Rs {float(action['price']):.2f}\n"
            "YES ayachal execute, NO ayachal cancel."
        )
    return (
        f"Price update preview:\nProduct: {action['product']}\nNew price: Rs {float(action['price']):.2f}\n"
        "Reply YES to execute or NO to cancel."
    )


def notify_customer_order_update(customer_phone: str, order: OrderRecord, style_hint: str = "english") -> None:
    if style_hint == "malayalam":
        message = f"നിങ്ങളുടെ order {order.orderId} ഇപ്പോൾ {order.status.replace('_', ' ')} ആണ്."
    elif style_hint == "manglish":
        message = f"Ningalude order {order.orderId} ippo {order.status.replace('_', ' ')} aanu."
    else:
        message = f"Your order {order.orderId} is now {order.status.replace('_', ' ')}."
    send_whatsapp_message(customer_phone, message)


def generate_vendor_acknowledgement(order: OrderRecord, style: str, status: str) -> str:
    human_status = status.replace("_", " ")
    if style == "malayalam":
        return f"Order {order.orderId} {human_status} ആയി update ചെയ്തു."
    if style == "manglish":
        return f"Order {order.orderId} {human_status} ayi update cheythu."
    return f"Order {order.orderId} updated to {human_status}."


def get_customer_broadcast_targets() -> List[str]:
    order_rows = fetch_all("SELECT DISTINCT customer_phone AS phone FROM orders")
    state_rows = fetch_all("SELECT DISTINCT phone FROM conversation_state WHERE role = 'customer'")
    combined = {row["phone"] for row in order_rows} | {row["phone"] for row in state_rows}
    return sorted(combined)

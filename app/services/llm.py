import json
import os
import re
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional


OPENAI_API_URL = "https://api.openai.com/v1/chat/completions"
DEFAULT_MODEL = os.getenv("OPENAI_MODEL", "gpt-4.1-mini")


def _post_openai(messages: List[Dict[str, str]], *, json_mode: bool = False) -> str:
    api_key = os.getenv("OPENAI_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("OPENAI_API_KEY is not configured.")

    payload: Dict[str, Any] = {
        "model": DEFAULT_MODEL,
        "messages": messages,
        "temperature": 0.1,
    }
    if json_mode:
        payload["response_format"] = {"type": "json_object"}

    request = urllib.request.Request(
        OPENAI_API_URL,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            data = json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="ignore")
        raise RuntimeError(f"OpenAI request failed: {exc.code} {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"OpenAI request failed: {exc.reason}") from exc

    return data["choices"][0]["message"]["content"].strip()


def _safe_json_completion(messages: List[Dict[str, str]]) -> Dict[str, Any]:
    content = _post_openai(messages, json_mode=True)
    return json.loads(content)


def detect_language_style(message: str) -> str:
    if re.search(r"[\u0D00-\u0D7F]", message):
        return "malayalam"

    lowered = message.lower()
    manglish_markers = [
        "cheyyu",
        "ayi",
        "undo",
        "venam",
        "nale",
        "innu",
        "entha",
        "aaki",
        "eduthu",
    ]
    if any(token in lowered for token in manglish_markers):
        return "manglish"
    return "english"


def is_confirmation_yes(message: str) -> bool:
    lowered = message.strip().lower()
    positive_tokens = {
        "yes",
        "y",
        "ok",
        "okay",
        "confirm",
        "confirmed",
        "proceed",
        "sure",
        "അതെ",
        "ശരി",
        "aam",
        "athe",
        "sheri",
    }
    return lowered in positive_tokens


def is_confirmation_no(message: str) -> bool:
    lowered = message.strip().lower()
    negative_tokens = {
        "no",
        "n",
        "cancel",
        "stop",
        "not now",
        "വേണ്ട",
        "ഇല്ല",
        "venda",
        "illa",
    }
    return lowered in negative_tokens


def _call_or_fallback(messages: List[Dict[str, str]], fallback: Dict[str, Any]) -> Dict[str, Any]:
    try:
        return _safe_json_completion(messages)
    except Exception:
        return fallback


def classify_intent(message: str) -> str:
    fallback = {"intent": _rule_based_customer_intent(message)}
    data = _call_or_fallback(
        [
            {
                "role": "system",
                "content": (
                    "Classify a WhatsApp customer message. Allowed intents: browse_products, place_order, "
                    "confirm_order, order_status, cancel_order, fallback. Return JSON only as "
                    "{\"intent\":\"...\"}."
                ),
            },
            {"role": "user", "content": message},
        ],
        fallback,
    )
    return data.get("intent", fallback["intent"])


def extract_items(message: str) -> List[Dict[str, Any]]:
    fallback_items = _rule_based_extract_items(message)
    data = _call_or_fallback(
        [
            {
                "role": "system",
                "content": (
                    "Extract order items from English, Malayalam, or Manglish WhatsApp text. "
                    "Return JSON only like {\"items\":[{\"name\":\"coffee\",\"qty\":2}]}. "
                    "If no items are present, return {\"items\":[]}."
                ),
            },
            {"role": "user", "content": message},
        ],
        {"items": fallback_items},
    )
    items = data.get("items", [])
    cleaned: List[Dict[str, Any]] = []
    for item in items:
        name = str(item.get("name", "")).strip().lower()
        qty = int(item.get("qty", 1) or 1)
        if name:
            cleaned.append({"name": name, "qty": max(1, qty)})
    return cleaned


def extract_address(message: str) -> Optional[str]:
    fallback = _rule_based_extract_address(message)
    data = _call_or_fallback(
        [
            {
                "role": "system",
                "content": (
                    "Extract a delivery address from the message. Return JSON only as "
                    "{\"address\":\"...\"}. If missing, use {\"address\":null}."
                ),
            },
            {"role": "user", "content": message},
        ],
        {"address": fallback},
    )
    address = data.get("address")
    if address is None:
        return None
    address = str(address).strip()
    return address or None


def classify_vendor_intent(message: str) -> str:
    fallback = {"intent": _rule_based_vendor_intent(message)}
    data = _call_or_fallback(
        [
            {
                "role": "system",
                "content": (
                    "Classify a vendor WhatsApp command. Allowed intents: accept_order, reject_order, "
                    "out_for_delivery, delivered, create_offer, broadcast_message, update_price, fallback. "
                    "Return JSON only as {\"intent\":\"...\"}."
                ),
            },
            {"role": "user", "content": message},
        ],
        fallback,
    )
    return data.get("intent", fallback["intent"])


def extract_vendor_action(message: str, *, intent: Optional[str] = None) -> Dict[str, Any]:
    inferred_intent = intent or classify_vendor_intent(message)
    fallback = _rule_based_vendor_action(message, inferred_intent)
    data = _call_or_fallback(
        [
            {
                "role": "system",
                "content": (
                    "Convert a vendor message into structured JSON. Always include the intent field. "
                    "Use fields when relevant: product, price, date, message, orderId. "
                    "Return JSON only."
                ),
            },
            {"role": "user", "content": f"Intent: {inferred_intent}\nMessage: {message}"},
        ],
        fallback,
    )
    data["intent"] = data.get("intent", inferred_intent)
    return data


def generate_marketing_message(request_text: str, style: str) -> str:
    try:
        return _post_openai(
            [
                {
                    "role": "system",
                    "content": (
                        "Write a short WhatsApp marketing message for a local business. "
                        f"Reply in {style}. Keep it persuasive, clear, and under 320 characters."
                    ),
                },
                {"role": "user", "content": request_text},
            ]
        )
    except Exception:
        return _fallback_marketing_message(request_text, style)


def generate_support_reply(message: str, style: str) -> str:
    try:
        return _post_openai(
            [
                {
                    "role": "system",
                    "content": (
                        "You are a commerce support assistant. "
                        f"Reply in {style}. Be concise, helpful, and ask one clear next question if needed."
                    ),
                },
                {"role": "user", "content": message},
            ]
        )
    except Exception:
        return localize_message("support_fallback", style)


def localize_message(key: str, style: str, **kwargs: str) -> str:
    templates = {
        "english": {
            "ask_items": "Please tell me what you'd like to order.",
            "ask_address": "Please share your delivery address.",
            "awaiting_yes_no": "There is a pending action. Please reply YES to confirm or NO to cancel.",
            "order_created": "Order confirmed. Order ID: {order_id}. Total: Rs {total}. We have sent it to the vendor.",
            "order_cancelled": "Your order {order_id} has been cancelled.",
            "no_orders": "I couldn't find any recent orders for this number.",
            "cannot_cancel": "This order can no longer be cancelled because it is already {status}.",
            "order_status": "Order {order_id} is currently {status}. Total: Rs {total}.",
            "cancel_preview": "Please confirm cancellation.\nOrder ID: {order_id}\nItems: {items}\nReply YES to cancel or NO to keep it.",
            "action_cancelled": "Cancelled. No changes were made.",
            "generic_error": "Something went wrong while processing that action.",
            "offer_created": "Offer saved for {product} at Rs {price}. Valid until: {date}.",
            "price_updated": "Price updated for {product}. New price: Rs {price}.",
            "broadcast_sent": "Broadcast sent to {count} recipients.",
            "vendor_no_target_order": "No matching order was found for that action.",
            "vendor_order_preview": (
                "Please confirm vendor action.\nOrder ID: {order_id}\nCustomer: {customer}\n"
                "Items: {items}\nNext status: {next_status}\nReply YES to execute or NO to cancel."
            ),
            "vendor_offer_missing_fields": "I need both product and price to create an offer.",
            "vendor_price_missing_fields": "I need both product and price to update pricing.",
            "vendor_fallback": "I could not understand that vendor action. Please rephrase it.",
            "support_fallback": "I can help with products, orders, tracking, and support. Tell me what you need.",
        },
        "manglish": {
            "ask_items": "Enthokke items venam ennu parayu.",
            "ask_address": "Delivery address ayachu tharamo?",
            "awaiting_yes_no": "Oru pending action undu. Confirm cheyyan YES, cancel cheyyan NO ayakku.",
            "order_created": "Order confirm ayi. Order ID: {order_id}. Total: Rs {total}. Vendor-ne notify cheythu.",
            "order_cancelled": "Ningalude order {order_id} cancel cheythu.",
            "no_orders": "Ee number-il recent order kandilla.",
            "cannot_cancel": "Ithu ippo cancel cheyyan pattilla, status {status} aanu.",
            "order_status": "Order {order_id} ippo {status} aanu. Total: Rs {total}.",
            "cancel_preview": "Order cancel cheyyano?\nOrder ID: {order_id}\nItems: {items}\nYES ayachal cancel, NO ayachal keep cheyyam.",
            "action_cancelled": "Cancel cheythu. Oru maattavum cheythilla.",
            "generic_error": "Action process cheyyumbo oru prashnam undayi.",
            "offer_created": "{product} offer Rs {price} ayi save cheythu. Valid until: {date}.",
            "price_updated": "{product} price update cheythu. Puthiya price: Rs {price}.",
            "broadcast_sent": "Broadcast {count} recipients-kku ayachu.",
            "vendor_no_target_order": "I action-inu match aya order kandilla.",
            "vendor_order_preview": (
                "Vendor action confirm cheyyu.\nOrder ID: {order_id}\nCustomer: {customer}\n"
                "Items: {items}\nNext status: {next_status}\nYES ayachal execute, NO ayachal cancel."
            ),
            "vendor_offer_missing_fields": "Offer create cheyyan productum priceum venam.",
            "vendor_price_missing_fields": "Price update cheyyan productum priceum venam.",
            "vendor_fallback": "Vendor action manasilayilla. Vere reethiyil parayu.",
            "support_fallback": "Products, order, tracking, support ellam njan sahayikkam. Ningalkku entha venam?",
        },
        "malayalam": {
            "ask_items": "എന്തെല്ലാം സാധനങ്ങളാണ് വേണ്ടത് എന്ന് പറയൂ.",
            "ask_address": "ഡെലിവറി അഡ്രസ് അയയ്ക്കാമോ?",
            "awaiting_yes_no": "ഒരു pending action ഉണ്ട്. YES അയച്ചാൽ confirm ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും.",
            "order_created": "ഓർഡർ confirm ആയി. Order ID: {order_id}. Total: Rs {total}. Vendor-നെ അറിയിച്ചു.",
            "order_cancelled": "താങ്കളുടെ order {order_id} cancel ചെയ്തു.",
            "no_orders": "ഈ നമ്പറിന് recent order ഒന്നും കണ്ടില്ല.",
            "cannot_cancel": "ഈ order ഇനി cancel ചെയ്യാൻ സാധിക്കില്ല. Status {status} ആണ്.",
            "order_status": "Order {order_id} ഇപ്പോൾ {status} ആണ്. Total: Rs {total}.",
            "cancel_preview": "Order cancel ചെയ്യണോ?\nOrder ID: {order_id}\nItems: {items}\nYES അയച്ചാൽ cancel ചെയ്യും, NO അയച്ചാൽ വെച്ചിരിക്കും.",
            "action_cancelled": "Cancel ചെയ്തു. മാറ്റങ്ങളൊന്നും വരുത്തിയിട്ടില്ല.",
            "generic_error": "Action process ചെയ്യുമ്പോൾ ഒരു പ്രശ്നം ഉണ്ടായി.",
            "offer_created": "{product} offer Rs {price} ആയി save ചെയ്തു. Valid until: {date}.",
            "price_updated": "{product} price update ചെയ്തു. പുതിയ price: Rs {price}.",
            "broadcast_sent": "Broadcast {count} recipients-ക്ക് അയച്ചു.",
            "vendor_no_target_order": "ഈ action-നു വേണ്ട order കണ്ടെത്താനായില്ല.",
            "vendor_order_preview": (
                "Vendor action confirm ചെയ്യൂ.\nOrder ID: {order_id}\nCustomer: {customer}\n"
                "Items: {items}\nNext status: {next_status}\nYES അയച്ചാൽ execute ചെയ്യും, NO അയച്ചാൽ cancel ചെയ്യും."
            ),
            "vendor_offer_missing_fields": "Offer create ചെയ്യാൻ productയും priceയും വേണം.",
            "vendor_price_missing_fields": "Price update ചെയ്യാൻ productയും priceയും വേണം.",
            "vendor_fallback": "Vendor action മനസ്സിലായില്ല. ദയവായി വീണ്ടും പറയൂ.",
            "support_fallback": "Products, orders, tracking, support എന്നിവയിൽ സഹായിക്കാം. എന്ത് വേണമെന്ന് പറയൂ.",
        },
    }
    selected = templates.get(style, templates["english"])
    return selected[key].format(**kwargs)


def _rule_based_customer_intent(message: str) -> str:
    lowered = message.lower()
    if any(word in lowered for word in ["track", "status", "where", "evide", "എവിടെ"]):
        return "order_status"
    if any(word in lowered for word in ["cancel", "cancel cheyyu", "വേണ്ട"]):
        return "cancel_order"
    if any(word in lowered for word in ["yes", "ok", "confirm", "athe", "ശരി"]):
        return "confirm_order"
    if any(word in lowered for word in ["order", "venam", "buy", "place", "കൊടുക്കൂ"]):
        return "place_order"
    if any(word in lowered for word in ["product", "menu", "price", "catalog", "undo"]):
        return "browse_products"
    return "fallback"


def _rule_based_extract_items(message: str) -> List[Dict[str, Any]]:
    pattern = re.compile(r"(?P<qty>\d+)\s*(?:x\s*)?(?P<name>[a-zA-Z\u0D00-\u0D7F ]+)")
    results: List[Dict[str, Any]] = []
    for match in pattern.finditer(message):
        name = match.group("name").strip(" ,.")
        qty = int(match.group("qty"))
        if name:
            results.append({"name": name.lower(), "qty": qty})
    if results:
        return results

    tokens = re.findall(r"\b[a-zA-Z]+\b", message.lower())
    stop_words = {"order", "please", "need", "want", "for", "to"}
    return [{"name": token, "qty": 1} for token in tokens[:3] if token not in stop_words]


def _rule_based_extract_address(message: str) -> Optional[str]:
    lowered = message.lower()
    markers = ["address", "addr", "near", "road", "street", "house", "building", "post", "pin"]
    if any(marker in lowered for marker in markers) and len(message.strip()) > 12:
        return message.strip()
    return None


def _rule_based_vendor_intent(message: str) -> str:
    lowered = message.lower()
    if any(token in lowered for token in ["dispatch", "out for delivery"]):
        return "out_for_delivery"
    if "deliver" in lowered:
        return "delivered"
    if any(token in lowered for token in ["reject", "decline"]):
        return "reject_order"
    if any(token in lowered for token in ["accept", "ok", "approve"]):
        return "accept_order"
    if any(token in lowered for token in ["offer", "discount"]):
        return "create_offer"
    if any(token in lowered for token in ["announce", "broadcast"]):
        return "broadcast_message"
    if any(token in lowered for token in ["price", "aaki", "update"]):
        return "update_price"
    return "fallback"


def _parse_relative_date(message: str) -> Optional[str]:
    lowered = message.lower()
    today = datetime.utcnow().date()
    if "tomorrow" in lowered or "nale" in lowered:
        return (today + timedelta(days=1)).isoformat()
    if "today" in lowered or "innu" in lowered:
        return today.isoformat()
    return None


def _rule_based_vendor_action(message: str, intent: str) -> Dict[str, Any]:
    action: Dict[str, Any] = {"intent": intent}
    price_match = re.search(r"(\d+(?:\.\d+)?)", message)
    if price_match:
        action["price"] = float(price_match.group(1))
    order_match = re.search(r"(?:order[\s#:]*)([A-Z0-9-]+)", message, flags=re.IGNORECASE)
    if order_match:
        action["orderId"] = order_match.group(1).upper()
    words = [word for word in re.findall(r"[a-zA-Z]+", message.lower()) if word not in {"offer", "price", "announce", "update"}]
    if words and intent in {"create_offer", "update_price"}:
        action["product"] = words[0]
    if intent == "broadcast_message":
        action["message"] = message.strip()
    relative_date = _parse_relative_date(message)
    if relative_date:
        action["date"] = relative_date
    return action


def _fallback_marketing_message(request_text: str, style: str) -> str:
    if style == "malayalam":
        return f"ഇന്നത്തെ പ്രത്യേക പ്രഖ്യാപനം: {request_text}. കൂടുതൽ വിവരങ്ങൾക്ക് reply ചെയ്യൂ."
    if style == "manglish":
        return f"Innathe special update: {request_text}. Details venel reply cheyyu."
    return f"Special update from our shop: {request_text}. Reply here for details and orders."

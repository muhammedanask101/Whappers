from __future__ import annotations

from typing import Any, Dict

from app.db.mongo import DEFAULT_BUSINESS_ID, get_collection


def style_text(style: str, english: str, manglish: str, malayalam: str) -> str:
    normalized = (style or "english").strip().lower()
    if normalized == "malayalam":
        return malayalam
    if normalized == "manglish":
        return manglish
    return english


def status_phrase(status: str, style: str) -> str:
    normalized = str(status or "").strip().lower()
    phrases = {
        "pending": {
            "english": "pending",
            "manglish": "pending aanu",
            "malayalam": "പെൻഡിംഗിലാണ്",
        },
        "accepted": {
            "english": "accepted",
            "manglish": "accept cheythu",
            "malayalam": "സ്വീകരിച്ചു",
        },
        "rejected": {
            "english": "rejected",
            "manglish": "reject cheythu",
            "malayalam": "നിരസിച്ചു",
        },
        "out_for_delivery": {
            "english": "out for delivery",
            "manglish": "deliverykkayi purappettu",
            "malayalam": "ഡെലിവറിയിലേക്ക് പുറപ്പെട്ടിട്ടുണ്ട്",
        },
        "delivered": {
            "english": "delivered",
            "manglish": "deliver ayi",
            "malayalam": "ഡെലിവർ ചെയ്തു",
        },
    }
    mapping = phrases.get(normalized, {})
    return mapping.get(style or "english", normalized.replace("_", " "))


def contact_style(phone: str, business_id: str = DEFAULT_BUSINESS_ID, default: str = "english") -> str:
    document = get_collection("conversation_state").find_one(
        {"business_id": business_id, "phone": phone},
        {"last_language": 1},
    )
    style = str((document or {}).get("last_language") or "").strip().lower()
    return style if style in {"english", "manglish", "malayalam"} else default


def format_cart_message(style: str, items: list[dict[str, Any]], address: str = "") -> str:
    if not items:
        return style_text(
            style,
            "Your cart is empty.",
            "Ningalude cart ippol empty aanu.",
            "നിങ്ങളുടെ കാർട്ട് ഇപ്പോൾ കാലിയാണ്.",
        )
    lines = [f"- {item['name'].title()} x {item['qty']}" for item in items]
    if address:
        lines.append(style_text(style, f"Address: {address}", f"Address: {address}", f"വിലാസം: {address}"))
    intro = style_text(
        style,
        "Current cart:",
        "Ippo cart-il ullathu:",
        "ഇപ്പോൾ കാർട്ടിലുള്ളത്:",
    )
    return intro + "\n" + "\n".join(lines)


def ask_address_text(style: str) -> str:
    return style_text(
        style,
        "Please share your delivery address whenever you're ready.",
        "Ready aayal delivery address ayachu tharuo.",
        "തയ്യാറായാൽ ഡെലിവറി വിലാസം അയച്ചു തരൂ.",
    )


def order_placed_text(style: str, order_id: str, total: float, status: str, address: str) -> str:
    return style_text(
        style,
        f"Order placed successfully.\nOrder ID: {order_id}\nTotal: Rs {total:.2f}\nStatus: {status_phrase(status, 'english')}\nAddress: {address}",
        f"Order successfully place cheythu.\nOrder ID: {order_id}\nTotal: Rs {total:.2f}\nStatus: {status_phrase(status, 'manglish')}\nAddress: {address}",
        f"ഓർഡർ വിജയകരമായി സ്ഥിരീകരിച്ചു.\nOrder ID: {order_id}\nആകെ: Rs {total:.2f}\nസ്റ്റാറ്റസ്: {status_phrase(status, 'malayalam')}\nവിലാസം: {address}",
    )


def order_status_text(style: str, order_id: str, status: str, items_summary: str, total: float) -> str:
    return style_text(
        style,
        f"Order {order_id}\nStatus: {status_phrase(status, 'english')}\nItems: {items_summary}\nTotal: Rs {total:.2f}",
        f"Order {order_id}\nStatus: {status_phrase(status, 'manglish')}\nItems: {items_summary}\nTotal: Rs {total:.2f}",
        f"Order {order_id}\nസ്റ്റാറ്റസ്: {status_phrase(status, 'malayalam')}\nഇനങ്ങൾ: {items_summary}\nആകെ: Rs {total:.2f}",
    )


def support_ticket_text(style: str, ticket_id: str, priority: str, updated: bool = False) -> str:
    if updated:
        return style_text(
            style,
            f"I've updated your open support case. Ticket ID: {ticket_id}.",
            f"Open support case update cheythu. Ticket ID: {ticket_id}.",
            f"നിങ്ങളുടെ ഓപ്പൺ സപ്പോർട്ട് കേസ് അപ്‌ഡേറ്റ് ചെയ്തു. Ticket ID: {ticket_id}.",
        )
    return style_text(
        style,
        f"Support ticket created. Ticket ID: {ticket_id}. Priority: {priority}.",
        f"Support ticket create cheythu. Ticket ID: {ticket_id}. Priority: {priority}.",
        f"സപ്പോർട്ട് ടിക്കറ്റ് സൃഷ്ടിച്ചു. Ticket ID: {ticket_id}. Priority: {priority}.",
    )


def recommendation_intro(style: str) -> str:
    return style_text(
        style,
        "You might like:",
        "Ithum ningalkku ishtappedam:",
        "ഇതും നിങ്ങൾക്ക് ഇഷ്ടപ്പെടാം:",
    )


def contact_reply_prompt(style: str) -> str:
    return style_text(
        style,
        "Tell me what you need help with: products, an order, or support.",
        "Products, order, allenkil support-il entha venam ennu parayu.",
        "ഉൽപ്പന്നങ്ങൾ, ഓർഡർ, അല്ലെങ്കിൽ സപ്പോർട്ടിൽ എന്ത് സഹായമാണ് വേണ്ടതെന്ന് പറയൂ.",
    )

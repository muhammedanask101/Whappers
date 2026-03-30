from __future__ import annotations

import re


def normalize_phone(number: str) -> str:
    raw = str(number or "").strip()
    if not raw:
        return ""
    if raw.startswith("whatsapp:"):
        normalized = raw[len("whatsapp:") :].strip()
    else:
        normalized = raw
    digits = re.sub(r"[^\d+]", "", normalized)
    if not digits:
        return ""
    if not digits.startswith("+"):
        digits = f"+{digits}"
    return f"whatsapp:{digits}"


def to_e164(number: str) -> str:
    normalized = normalize_phone(number)
    if not normalized:
        return ""
    return normalized.split(":", 1)[1]


def to_meta_recipient(number: str) -> str:
    e164 = to_e164(number)
    return e164.lstrip("+")

from __future__ import annotations

import hashlib
import hmac
import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request

from app.core.settings import get_settings
from app.services.messaging.utils import normalize_phone, to_meta_recipient


def _log_meta(event: str, **details: Any) -> None:
    normalized = " ".join(f"{key}={repr(value)}" for key, value in details.items())
    print(f"[messaging.meta] {event} {normalized}".rstrip())


class MetaProviderError(RuntimeError):
    def __init__(self, message: str, *, retryable: bool, status_code: int | None = None) -> None:
        super().__init__(message)
        self.retryable = retryable
        self.status_code = status_code


class MetaProvider:
    provider_name = "meta"

    def send_text(self, to: str, text: str, from_number: str = "") -> Dict[str, Any]:
        settings = get_settings()
        access_token = settings.meta_whatsapp_access_token
        phone_number_id = settings.meta_whatsapp_phone_number_id
        normalized_to = normalize_phone(to)
        meta_to = to_meta_recipient(to)
        if not all([access_token, phone_number_id, meta_to]):
            raise MetaProviderError(
                "Meta WhatsApp Cloud API environment variables are not fully configured.",
                retryable=False,
            )

        url = f"https://graph.facebook.com/{settings.meta_graph_api_version}/{phone_number_id}/messages"
        body = json.dumps(
            {
                "messaging_product": "whatsapp",
                "to": meta_to,
                "type": "text",
                "text": {"body": text},
            }
        ).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=body,
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )

        try:
            _log_meta("send_request", to=normalized_to, body_chars=len(text), phone_number_id=phone_number_id)
            with urllib.request.urlopen(request, timeout=30) as response:
                data = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")
            _log_meta("send_http_error", to=normalized_to, status=exc.code, detail=detail[:240])
            raise MetaProviderError(
                f"Meta message send failed: {exc.code} {detail}",
                retryable=exc.code in {408, 409, 429, 500, 502, 503, 504},
                status_code=exc.code,
            ) from exc
        except urllib.error.URLError as exc:
            _log_meta("send_network_error", to=normalized_to, error=str(exc.reason))
            raise MetaProviderError(f"Meta message send failed: {exc.reason}", retryable=True) from exc

        message_id = ((data.get("messages") or [{}])[0] or {}).get("id")
        _log_meta("send_success", to=normalized_to, message_id=message_id)
        return {
            "success": True,
            "provider": self.provider_name,
            "to": normalized_to,
            "from": normalize_phone(from_number),
            "message_id": message_id,
            "contacts": data.get("contacts", []),
            "messages": data.get("messages", []),
            "raw": data,
        }

    def parse_incoming(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        value = self._extract_change_value(payload)
        statuses = value.get("statuses") or []
        if statuses:
            status = statuses[0] or {}
            conversation = status.get("conversation") or {}
            pricing = status.get("pricing") or {}
            return {
                "from": normalize_phone(str(status.get("recipient_id") or "")),
                "to": normalize_phone(str((value.get("metadata") or {}).get("display_phone_number") or "")),
                "message": "",
                "type": "status",
                "message_id": str(status.get("id") or "").strip(),
                "status": str(status.get("status") or "").strip(),
                "event_type": "status",
                "status_payload": {
                    "status": str(status.get("status") or "").strip(),
                    "message_id": str(status.get("id") or "").strip(),
                    "recipient_id": normalize_phone(str(status.get("recipient_id") or "")),
                    "timestamp": str(status.get("timestamp") or "").strip(),
                    "conversation_id": str(conversation.get("id") or "").strip(),
                    "origin_type": str((conversation.get("origin") or {}).get("type") or "").strip(),
                    "billable": bool(pricing.get("billable", False)),
                    "category": str(pricing.get("category") or "").strip(),
                    "raw": status,
                },
                "raw": payload,
            }
        messages = value.get("messages") or []
        if not messages:
            raise HTTPException(status_code=400, detail="Invalid Meta WhatsApp webhook payload.")
        message = messages[0] or {}
        sender = normalize_phone(str(message.get("from") or ""))
        message_type = str(message.get("type") or "").strip() or "text"
        text_body = ""
        if message_type == "text":
            text_body = str(((message.get("text") or {}).get("body") or "")).strip()
        elif message_type == "button":
            text_body = str(((message.get("button") or {}).get("text") or "")).strip()
        elif message_type == "interactive":
            interactive = message.get("interactive") or {}
            button_reply = interactive.get("button_reply") or {}
            list_reply = interactive.get("list_reply") or {}
            text_body = str(button_reply.get("title") or list_reply.get("title") or "").strip()
        if not sender:
            raise HTTPException(status_code=400, detail="Missing Meta WhatsApp sender.")
        return {
            "from": sender,
            "to": normalize_phone(str((value.get("metadata") or {}).get("display_phone_number") or "")),
            "message": text_body,
            "type": message_type,
            "event_type": "message",
            "message_id": str(message.get("id") or "").strip(),
            "raw": payload,
        }

    async def validate_webhook(self, request: Request) -> Optional[str]:
        settings = get_settings()
        method = request.method.upper()
        if method == "GET":
            mode = request.query_params.get("hub.mode", "")
            token = request.query_params.get("hub.verify_token", "")
            challenge = request.query_params.get("hub.challenge", "")
            if mode == "subscribe" and challenge and token == settings.meta_whatsapp_verify_token:
                return challenge
            raise HTTPException(status_code=403, detail="Webhook verification failed.")
        if method != "POST":
            return None
        if not settings.meta_app_secret:
            if settings.environment in {"prod", "production"}:
                raise HTTPException(status_code=500, detail="META_APP_SECRET is required for webhook verification.")
            return None
        raw_body = await request.body()
        signature = str(request.headers.get("X-Hub-Signature-256") or "").strip()
        if not signature.startswith("sha256="):
            raise HTTPException(status_code=403, detail="Webhook signature missing.")
        expected = hmac.new(settings.meta_app_secret.encode("utf-8"), raw_body, hashlib.sha256).hexdigest()
        if not hmac.compare_digest(signature, f"sha256={expected}"):
            raise HTTPException(status_code=403, detail="Webhook signature invalid.")
        return None

    def _extract_change_value(self, payload: Dict[str, Any]) -> Dict[str, Any]:
        try:
            entry = (payload.get("entry") or [])[0] or {}
            change = (entry.get("changes") or [])[0] or {}
            value = change.get("value") or {}
        except (AttributeError, IndexError, TypeError):
            value = {}
        if not isinstance(value, dict):
            raise HTTPException(status_code=400, detail="Invalid Meta WhatsApp webhook payload.")
        return value

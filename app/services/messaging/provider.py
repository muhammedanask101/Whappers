from __future__ import annotations

from typing import Optional

from app.services.messaging.meta_provider import MetaProvider


class MessagingProvider:
    def send_text(self, to: str, text: str, from_number: str = "") -> dict:
        raise NotImplementedError

    def parse_incoming(self, payload: dict) -> dict:
        raise NotImplementedError

    async def validate_webhook(self, request) -> Optional[str]:
        raise NotImplementedError


def get_messaging_provider() -> MessagingProvider:
    return MetaProvider()

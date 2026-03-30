from __future__ import annotations

import asyncio
import json
import os
import sys
import types
import unittest
import urllib.error
from types import SimpleNamespace
from unittest.mock import patch

if "fastapi" not in sys.modules:
    fastapi = types.ModuleType("fastapi")

    class _HTTPException(Exception):
        def __init__(self, status_code=500, detail=""):
            super().__init__(detail)
            self.status_code = status_code
            self.detail = detail

    class _Response:
        def __init__(self, content="", media_type=None, status_code=200):
            self.headers = {}
            self.content = content
            self.media_type = media_type
            self.status_code = status_code

        def set_cookie(self, key, value, **kwargs):
            self.headers["set-cookie"] = f"{key}={value}; Path=/"

        def delete_cookie(self, key, **kwargs):
            self.headers["set-cookie"] = f"{key}=; Path=/"

    class _APIRouter:
        def __init__(self, *args, **kwargs):
            return None

        def get(self, *args, **kwargs):
            return lambda fn: fn

        post = put = delete = get

    fastapi.APIRouter = _APIRouter
    fastapi.Cookie = lambda *args, **kwargs: None
    fastapi.Depends = lambda dependency=None: dependency
    fastapi.Form = lambda default="": default
    fastapi.Header = lambda default=None, **kwargs: default
    fastapi.HTTPException = _HTTPException
    fastapi.Request = object
    fastapi.Response = _Response
    fastapi.status = types.SimpleNamespace(HTTP_400_BAD_REQUEST=400, HTTP_404_NOT_FOUND=404)
    sys.modules["fastapi"] = fastapi

from fastapi import HTTPException

from app.core.settings import get_settings
from app.services import resilience as resilience_mod
from app.services.messaging.meta_provider import MetaProvider
from app.services.messaging.provider import get_messaging_provider
from app.services.messaging.utils import normalize_phone


class _FakeHttpResponse:
    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        return False


class MetaProviderTests(unittest.TestCase):
    def tearDown(self) -> None:
        get_settings.cache_clear()
        resilience_mod._CALLERS.clear()

    def test_meta_parse_incoming_normalizes_to_internal_format(self) -> None:
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"display_phone_number": "+91 99999 99999"},
                                "messages": [
                                    {
                                        "id": "wamid.abc",
                                        "from": "919876543210",
                                        "type": "text",
                                        "text": {"body": "Hello from Meta"},
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        parsed = MetaProvider().parse_incoming(payload)
        self.assertEqual(parsed["from"], "whatsapp:+919876543210")
        self.assertEqual(parsed["to"], "whatsapp:+919999999999")
        self.assertEqual(parsed["message"], "Hello from Meta")
        self.assertEqual(parsed["type"], "text")
        self.assertEqual(parsed["message_id"], "wamid.abc")

    def test_meta_validate_webhook_returns_challenge(self) -> None:
        request = SimpleNamespace(
            method="GET",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "verify-me",
                "hub.challenge": "challenge-token",
            },
        )
        with patch.dict(os.environ, {"META_WHATSAPP_VERIFY_TOKEN": "verify-me"}, clear=False):
            get_settings.cache_clear()
            result = asyncio.run(MetaProvider().validate_webhook(request))
        self.assertEqual(result, "challenge-token")

    def test_meta_send_text_success(self) -> None:
        provider = MetaProvider()
        with patch.dict(
            os.environ,
            {
                "META_WHATSAPP_ACCESS_TOKEN": "meta-token",
                "META_WHATSAPP_PHONE_NUMBER_ID": "12345",
                "META_GRAPH_API_VERSION": "v19.0",
            },
            clear=False,
        ):
            get_settings.cache_clear()
            with patch("urllib.request.urlopen", return_value=_FakeHttpResponse({"messages": [{"id": "wamid.sent"}], "contacts": [{"wa_id": "919876543210"}]})):
                result = provider.send_text("whatsapp:+919876543210", "Hello")
        self.assertTrue(result["success"])
        self.assertEqual(result["provider"], "meta")
        self.assertEqual(result["message_id"], "wamid.sent")
        self.assertEqual(result["to"], normalize_phone("919876543210"))

    def test_meta_parse_status_event(self) -> None:
        payload = {
            "entry": [
                {
                    "changes": [
                        {
                            "value": {
                                "metadata": {"display_phone_number": "+91 99999 99999"},
                                "statuses": [
                                    {
                                        "id": "wamid.sent",
                                        "status": "delivered",
                                        "recipient_id": "919876543210",
                                        "timestamp": "1710000000",
                                    }
                                ],
                            }
                        }
                    ]
                }
            ]
        }
        parsed = MetaProvider().parse_incoming(payload)
        self.assertEqual(parsed["event_type"], "status")
        self.assertEqual(parsed["status"], "delivered")
        self.assertEqual(parsed["message_id"], "wamid.sent")

    def test_provider_factory_defaults_to_meta(self) -> None:
        with patch.dict(os.environ, {"MESSAGING_PROVIDER": "meta"}, clear=False):
            get_settings.cache_clear()
            provider = get_messaging_provider()
        self.assertEqual(getattr(provider, "provider_name", ""), "meta")

    def test_meta_validate_webhook_rejects_bad_token(self) -> None:
        request = SimpleNamespace(
            method="GET",
            query_params={
                "hub.mode": "subscribe",
                "hub.verify_token": "wrong",
                "hub.challenge": "challenge-token",
            },
        )
        with patch.dict(os.environ, {"META_WHATSAPP_VERIFY_TOKEN": "verify-me"}, clear=False):
            get_settings.cache_clear()
            with self.assertRaises(HTTPException):
                asyncio.run(MetaProvider().validate_webhook(request))


if __name__ == "__main__":
    unittest.main()

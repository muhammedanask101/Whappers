from __future__ import annotations

import asyncio
import os
import sys
import types
import unittest
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
            self.status_code = status_code
            self.content = content
            self.media_type = media_type

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

if "slowapi" not in sys.modules:
    slowapi = types.ModuleType("slowapi")

    class _Limiter:
        def __init__(self, *args, **kwargs):
            return None

        def limit(self, *args, **kwargs):
            return lambda fn: fn

    slowapi.Limiter = _Limiter
    sys.modules["slowapi"] = slowapi
    slowapi_util = types.ModuleType("slowapi.util")
    slowapi_util.get_remote_address = lambda request: "test"
    sys.modules["slowapi.util"] = slowapi_util

if "pydantic" not in sys.modules:
    pydantic = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    pydantic.BaseModel = _BaseModel
    pydantic.Field = lambda default=None, **kwargs: default
    sys.modules["pydantic"] = pydantic

if "pymongo" not in sys.modules:
    pymongo = types.ModuleType("pymongo")
    pymongo.MongoClient = object
    pymongo.ReturnDocument = types.SimpleNamespace(AFTER="after")
    sys.modules["pymongo"] = pymongo
    pymongo_collection = types.ModuleType("pymongo.collection")
    pymongo_collection.Collection = object
    sys.modules["pymongo.collection"] = pymongo_collection
    pymongo_database = types.ModuleType("pymongo.database")
    pymongo_database.Database = object
    sys.modules["pymongo.database"] = pymongo_database

if "bcrypt" not in sys.modules:
    bcrypt = types.ModuleType("bcrypt")
    bcrypt.gensalt = lambda: b"salt"
    bcrypt.hashpw = lambda password, salt: b"$2stubbedhash"
    bcrypt.checkpw = lambda password, hashed: True
    sys.modules["bcrypt"] = bcrypt

from fastapi import HTTPException, Response

from app.routes import onboarding as onboarding_route
from app.routes import whatsapp as whatsapp_route
from app.services import auth as auth_mod


class _FakeCollection:
    def __init__(self) -> None:
        self.docs: list[dict] = []

    def create_index(self, *args, **kwargs) -> None:
        return None

    def find_one(self, query: dict) -> dict | None:
        for row in self.docs:
            if all(row.get(key) == value for key, value in query.items()):
                return dict(row)
        return None

    def insert_one(self, document: dict) -> None:
        self.docs.append(dict(document))

    def update_one(self, query: dict, update: dict, upsert: bool = False) -> None:
        for row in self.docs:
            if all(row.get(key) == value for key, value in query.items()):
                if "$set" in update:
                    row.update(update["$set"])
                if "$setOnInsert" in update and not row:
                    row.update(update["$setOnInsert"])
                return
        if upsert:
            document = dict(query)
            document.update(update.get("$setOnInsert", {}))
            document.update(update.get("$set", {}))
            self.docs.append(document)


class ApiSecurityAndFlowTests(unittest.TestCase):
    def setUp(self) -> None:
        auth_mod.get_settings.cache_clear()

    def tearDown(self) -> None:
        auth_mod.get_settings.cache_clear()

    def test_refresh_token_rotation_and_logout_revocation(self) -> None:
        collections = {
            "users": _FakeCollection(),
            "refresh_tokens": _FakeCollection(),
            "subscriptions": _FakeCollection(),
            "onboarding_states": _FakeCollection(),
        }
        user_document = {
            "user_id": "user_1",
            "email": "owner@example.com",
            "name": "Owner",
            "role": auth_mod.ROLE_BUSINESS_ADMIN,
            "business_id": "biz_1",
            "active": True,
            "password_hash": auth_mod.hash_password("secret-pass"),
        }
        collections["users"].docs.append(user_document)

        with patch.dict(os.environ, {"MANAGER_SESSION_SECRET": "test-secret", "ENV": "dev"}, clear=False):
            with patch.object(auth_mod, "get_collection", side_effect=lambda name: collections[name]):
                response = Response()
                issued = auth_mod.issue_auth_tokens(response, auth_mod._serialize_user(user_document))
                original_token = response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
                self.assertEqual(len(collections["refresh_tokens"].docs), 1)

                refreshed_response = Response()
                refreshed = auth_mod.refresh_auth_tokens(refreshed_response, original_token)
                self.assertEqual(refreshed["user"]["email"], "owner@example.com")
                self.assertEqual(len(collections["refresh_tokens"].docs), 2)
                self.assertIsNotNone(collections["refresh_tokens"].docs[0].get("revoked_at"))

                rotated_token = refreshed_response.headers["set-cookie"].split("=", 1)[1].split(";", 1)[0]
                logout_response = Response()
                auth_mod.revoke_refresh_token(logout_response, rotated_token)
                self.assertIsNotNone(collections["refresh_tokens"].docs[1].get("revoked_at"))

    def test_api_user_rejects_legacy_admin_header(self) -> None:
        with self.assertRaises(HTTPException):
            auth_mod.get_current_api_user(authorization=None)

    def test_tenant_scope_blocks_cross_business_access(self) -> None:
        with self.assertRaises(HTTPException):
            auth_mod.ensure_business_scope(
                {"role": auth_mod.ROLE_BUSINESS_ADMIN, "business_id": "biz_a"},
                "biz_b",
            )

    def test_onboarding_route_updates_state(self) -> None:
        recorded: dict[str, str] = {}
        with patch.object(onboarding_route, "create_business_with_subscription", return_value={"business_id": "biz_new", "name": "New Shop"}):
            with patch.object(onboarding_route, "update_onboarding_state", side_effect=lambda business_id, step: recorded.update({"business_id": business_id, "step": step})):
                result = onboarding_route.onboard(onboarding_route.OnboardRequest(name="New Shop", phone="+911234567890"))
        self.assertTrue(result["success"])
        self.assertEqual(recorded, {"business_id": "biz_new", "step": "signup"})

    def test_whatsapp_webhook_gracefully_falls_back_on_agent_failure(self) -> None:
        async def _verify(_request) -> bool:
            return True

        async def _raise(*args, **kwargs):
            raise RuntimeError("llm unavailable")

        async def _send(*args, **kwargs):
            return {"success": True}

        class _Provider:
            provider_name = "meta"

            def parse_incoming(self, payload):
                return {
                    "from": "whatsapp:+911234567890",
                    "to": "whatsapp:+919999999999",
                    "message": "hello",
                    "type": "text",
                    "message_id": "wamid.123",
                }

        whatsapp_route._RECENT_WEBHOOKS.clear()
        with patch.object(whatsapp_route, "verify_request", side_effect=_verify):
            with patch.object(whatsapp_route, "get_messaging_provider", return_value=_Provider()):
                with patch.object(whatsapp_route, "_read_incoming_payload", return_value={"entry": []}):
                        with patch.object(whatsapp_route, "_resolve_business", return_value={"business_id": "biz_1", "name": "Shop", "vendor_phone": "whatsapp:+91123", "whatsapp_number": "whatsapp:+91999"}):
                            with patch.object(whatsapp_route, "_reserve_distributed_dedupe_key", return_value=True):
                                with patch.object(whatsapp_route, "process_incoming_message", side_effect=_raise):
                                    with patch.object(whatsapp_route, "track_last_user_interaction", return_value=None):
                                        with patch.object(whatsapp_route, "send_whatsapp_message_async", side_effect=_send) as send_mock:
                                            response = asyncio.run(
                                                whatsapp_route.whatsapp_webhook(
                                                    request=SimpleNamespace(url="https://example.com/webhook/whatsapp", headers={"content-type": "application/json"}),
                                                )
                                            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.args[1], "We received your message. Please try again shortly.")

    def test_whatsapp_webhook_meta_payload_reaches_agent_with_normalized_message(self) -> None:
        async def _verify(_request) -> bool:
            return True

        async def _process(message, user):
            self.assertEqual(message, "Need 2 coffees")
            self.assertEqual(user["phone"], "whatsapp:+919876543210")
            self.assertEqual(user["role"], "customer")
            self.assertEqual(user["business_id"], "biz_1")
            return "Order noted"

        async def _send(*args, **kwargs):
            return {"success": True, "provider": "meta"}

        class _Provider:
            provider_name = "meta"

            def parse_incoming(self, payload):
                return {
                    "from": "whatsapp:+919876543210",
                    "to": "whatsapp:+919999999999",
                    "message": "Need 2 coffees",
                    "type": "text",
                    "message_id": "wamid.456",
                }

        whatsapp_route._RECENT_WEBHOOKS.clear()
        with patch.object(whatsapp_route, "verify_request", side_effect=_verify):
            with patch.object(whatsapp_route, "get_messaging_provider", return_value=_Provider()):
                with patch.object(whatsapp_route, "_read_incoming_payload", return_value={"entry": []}):
                        with patch.object(whatsapp_route, "_resolve_business", return_value={"business_id": "biz_1", "name": "Shop", "vendor_phone": "whatsapp:+91123", "whatsapp_number": "whatsapp:+91999"}):
                            with patch.object(whatsapp_route, "_reserve_distributed_dedupe_key", return_value=True):
                                with patch.object(whatsapp_route, "process_incoming_message", side_effect=_process):
                                    with patch.object(whatsapp_route, "track_last_user_interaction", return_value=None):
                                        with patch.object(whatsapp_route, "send_whatsapp_message_async", side_effect=_send) as send_mock:
                                            response = asyncio.run(
                                                whatsapp_route.whatsapp_webhook(
                                                    request=SimpleNamespace(url="https://example.com/webhook/whatsapp", headers={"content-type": "application/json"}),
                                                )
                                            )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(send_mock.call_args.args[0], "whatsapp:+919876543210")
        self.assertEqual(send_mock.call_args.args[1], "Order noted")

    def test_whatsapp_deduplication_uses_distributed_reservation(self) -> None:
        whatsapp_route._RECENT_WEBHOOKS.clear()
        with patch.object(whatsapp_route, "_reserve_distributed_dedupe_key", side_effect=[True, False]):
            first = whatsapp_route._is_duplicate_webhook("SMX", "whatsapp:+91123", "Hello")
            second = whatsapp_route._is_duplicate_webhook("SMX", "whatsapp:+91123", "Hello")
        self.assertFalse(first)
        self.assertTrue(second)


if __name__ == "__main__":
    unittest.main()

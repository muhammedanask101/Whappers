from __future__ import annotations

import sys
import types
import unittest

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

if "pydantic" not in sys.modules:
    pydantic = types.ModuleType("pydantic")

    class _BaseModel:
        def __init__(self, **kwargs):
            for key, value in kwargs.items():
                setattr(self, key, value)

    def _field(default=None, **kwargs):
        return default

    pydantic.BaseModel = _BaseModel
    pydantic.Field = _field
    sys.modules["pydantic"] = pydantic

from app.services.agent.memory import DEFAULT_STATE, update_goal_tracking
from app.services.agent.planner import choose_next_action
from app.services.intelligence.recommendations import recommend_products


class AgentSimulationTests(unittest.TestCase):
    def test_goal_tracking_progresses_after_successful_step(self) -> None:
        state = dict(DEFAULT_STATE)
        state["goal_tracking"] = update_goal_tracking(state, intent="checkout", user_message="place my order")
        state["goal_tracking"] = update_goal_tracking(
            state,
            intent="checkout",
            user_message="place my order",
            decision={
                "plan_steps": [{"action": "collect_address", "arguments": {}, "reason": "need address"}],
                "goal": {"goal_type": "checkout", "goal_text": "place order", "status": "active"},
            },
        )
        updated = update_goal_tracking(
            {"goal_tracking": state["goal_tracking"], "last_order_id": ""},
            intent="checkout",
            user_message="my address is here",
            observation={"tool": "collect_address", "ok": True},
        )
        self.assertIn("collect_address", updated["completed_steps"])
        self.assertGreaterEqual(updated["progress_score"], 1.0)

    def test_persisted_plan_is_reused_before_replanning(self) -> None:
        state = dict(DEFAULT_STATE)
        state["lastLanguage"] = "english"
        state["goal_tracking"] = {
            "goal_type": "support",
            "goal_text": "resolve issue",
            "status": "active",
            "current_plan": [{"action": "inspect_support_context", "arguments": {"raw_message": "my order is late"}, "reason": "load active ticket"}],
            "completed_steps": [],
            "progress_score": 0.0,
        }
        decision = choose_next_action(
            user_message="my order is late",
            user={"role": "customer", "phone": "1", "business_id": "biz_default"},
            state=state,
            memory_payload={"style": "english"},
            analysis={"intent": "support", "category": "support", "uncertainty": 0.5, "needs_llm": True},
            strategy={"mode": "support", "llm_tier": "balanced"},
            learning_profile={},
            scratchpad=[],
            max_steps=5,
        )
        self.assertEqual(decision.action, "inspect_support_context")

    def test_recommendation_engine_adds_bandit_signals(self) -> None:
        import app.services.intelligence.recommendations as recs

        original_profile = recs.get_customer_profile
        original_learning = recs.get_learning_profile
        original_catalog = recs.get_product_catalog
        try:
            recs.get_customer_profile = lambda phone, business_id: {"favorite_products": [], "product_affinity": {}, "segment": "new"}
            recs.get_learning_profile = lambda phone, business_id: {
                "click_signals": {},
                "recommendation_stats": {
                    "known": {"shown": 20, "clicked": 8, "ordered": 3},
                    "new": {"shown": 0, "clicked": 0, "ordered": 0},
                },
            }
            recs.get_product_catalog = lambda business_id, limit=50: [
                {"name": "known", "display_name": "Known", "description": "known item", "tags": ["known"], "updated_at": None},
                {"name": "new", "display_name": "New", "description": "new item", "tags": ["new"], "updated_at": None},
            ]
            rows = recommend_products("1", "biz_default", query="something")
            self.assertEqual(len(rows), 2)
            self.assertIn("exploration_bandit", rows[0]["ranking"])
            self.assertIn("exploitation", rows[0]["ranking"])
        finally:
            recs.get_customer_profile = original_profile
            recs.get_learning_profile = original_learning
            recs.get_product_catalog = original_catalog


if __name__ == "__main__":
    unittest.main()

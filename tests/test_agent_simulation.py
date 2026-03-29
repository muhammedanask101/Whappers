from __future__ import annotations

import asyncio
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
from app.services.agent.policy import analyze_message, cheap_action_allowed, select_cost_tier
from app.services.agent.planner import choose_next_action
from app.services.agent.types import PlannerDecision, ToolObservation
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

    def test_ambiguous_turn_does_not_force_collect_address(self) -> None:
        import app.services.agent.planner as planner_mod

        original_rank_tools = planner_mod.rank_tools
        original_call_agent_llm = planner_mod.call_agent_llm
        try:
            planner_mod.rank_tools = lambda **kwargs: [{"name": "repair_order_context"}, {"name": "inspect_order_context"}]
            planner_mod.call_agent_llm = lambda messages, tier="balanced": {"action": "respond", "arguments": {}, "message": "clarify", "confidence": 0.6}
            state = dict(DEFAULT_STATE)
            state["goal_tracking"] = {
                "goal_type": "checkout",
                "goal_text": "collect address",
                "status": "active",
                "current_plan": [{"action": "collect_address", "arguments": {}, "reason": "need address"}],
                "completed_steps": [],
                "progress_score": 0.0,
            }
            state["workflow"] = {"name": "order_checkout", "stage": "collect_address"}
            state["cart"] = {"items": [{"name": "tea", "qty": 1}], "address": ""}
            decision = choose_next_action(
                user_message="Yeah show it",
                user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                state=state,
                memory_payload={"style": "english"},
                analysis={"intent": "checkout", "category": "order", "uncertainty": 0.6, "needs_llm": True, "probable_address": False, "is_correction": False},
                strategy={"mode": "order", "llm_tier": "balanced"},
                learning_profile={},
                scratchpad=[],
                max_steps=5,
            )
            self.assertNotEqual(decision.action, "collect_address")
        finally:
            planner_mod.rank_tools = original_rank_tools
            planner_mod.call_agent_llm = original_call_agent_llm

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

    def test_greeting_maps_to_general_intent(self) -> None:
        analysis = analyze_message("Hey theree", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "general")

    def test_new_order_phrase_does_not_map_to_order_status(self) -> None:
        analysis = analyze_message("I want to make a new order", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertNotEqual(analysis["intent"], "order_status")

    def test_what_do_you_have_maps_to_browse(self) -> None:
        analysis = analyze_message("What do you have?", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

    def test_see_all_products_maps_to_browse_not_cart(self) -> None:
        analysis = analyze_message("I want to see all the products", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

    def test_general_and_browse_turns_are_cheap_when_safe(self) -> None:
        state = {"workflow": {}, "goal_tracking": {}}
        analysis = analyze_message("What do you have?", user={"role": "customer"}, state=state, learning_profile={})
        self.assertTrue(cheap_action_allowed(analysis, {"role": "customer"}, state))
        self.assertEqual(select_cost_tier(analysis, state, {"role": "customer"}), "cheap")

    def test_browse_fallback_does_not_choose_repair_tool(self) -> None:
        import app.services.agent.planner as planner_mod

        original_rank_tools = planner_mod.rank_tools
        original_call_agent_llm = planner_mod.call_agent_llm
        try:
            planner_mod.rank_tools = lambda **kwargs: [
                {"name": "search_catalog"},
                {"name": "manage_cart"},
                {"name": "repair_order_context"},
            ]

            def _raise(*args, **kwargs):
                raise RuntimeError("quota")

            planner_mod.call_agent_llm = _raise
            state = dict(DEFAULT_STATE)
            decision = choose_next_action(
                user_message="What products do you have?",
                user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                state=state,
                memory_payload={"style": "english"},
                analysis={"intent": "browse", "category": "sales", "uncertainty": 0.32, "needs_llm": True, "confidence": 0.48, "is_correction": False},
                strategy={"mode": "sales", "llm_tier": "smart"},
                learning_profile={},
                scratchpad=[],
                max_steps=5,
            )
            self.assertEqual(decision.action, "search_catalog")
        finally:
            planner_mod.rank_tools = original_rank_tools
            planner_mod.call_agent_llm = original_call_agent_llm

    def test_successful_user_facing_tool_response_stops_loop(self) -> None:
        import app.services.agent.main_agent as main_mod

        original_choose_next_action = main_mod.choose_next_action
        original_execute_tool = main_mod.execute_tool
        try:
            call_count = {"value": 0}

            def _decision(**kwargs):
                call_count["value"] += 1
                return PlannerDecision(action="repair_order_context", arguments={"raw_message": kwargs["user_message"]}, confidence=0.42)

            def _tool(*args, **kwargs):
                return ToolObservation(tool="repair_order_context", ok=True, message="Tell me what should be fixed in the order.")

            main_mod.choose_next_action = _decision
            main_mod.execute_tool = _tool

            result = asyncio.run(
                main_mod.run_agent(
                    user_message="What products do you have?",
                    user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                    state=dict(DEFAULT_STATE),
                    memory_payload={"style": "english"},
                    profile={},
                    learning_profile={},
                    style="english",
                )
            )
            self.assertEqual(result.reply, "Tell me what should be fixed in the order.")
            self.assertEqual(call_count["value"], 1)
        finally:
            main_mod.choose_next_action = original_choose_next_action
            main_mod.execute_tool = original_execute_tool


if __name__ == "__main__":
    unittest.main()

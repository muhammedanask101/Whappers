from __future__ import annotations

import asyncio
import sys
import types
import unittest
from types import SimpleNamespace
from unittest.mock import patch

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
from app.services.agent.policy import analyze_message, build_strategy, cheap_action_allowed, select_cost_tier
from app.services.agent.planner import choose_next_action, should_interrupt_workflow, decide_action_without_llm, route_by_intent, fallback_by_intent
from app.services.agent.types import PlannerDecision, ToolObservation
from app.services.agent.tools import execute_tool, rank_tools
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

    def test_show_my_order_maps_to_order_status(self) -> None:
        analysis = analyze_message("show my order", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "order_status")

    def test_confirm_routes_to_checkout_order_in_ready_state(self) -> None:
        action = route_by_intent("checkout", "confirm", {"workflow": {"stage": "ready_to_checkout"}, "cart": {}, "analysis": {}})
        self.assertEqual(action, "checkout_order")

    def test_see_all_products_maps_to_browse_not_cart(self) -> None:
        analysis = analyze_message("I want to see all the products", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

    def test_show_me_what_products_you_have_maps_to_browse(self) -> None:
        analysis = analyze_message("show me what products you have", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

    def test_i_want_to_see_products_maps_to_browse(self) -> None:
        analysis = analyze_message("I want to see products", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

    def test_catalog_search_recovers_to_broad_listing(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        try:
            def _catalog(**kwargs):
                if kwargs.get("search"):
                    return []
                return [
                    {"name": "hoodie", "display_name": "Hoodie", "description": "Warm hoodie", "price": 500, "offer_price": None},
                    {"name": "boots", "display_name": "Boots", "description": "Durable boots", "price": 800, "offer_price": None},
                ]

            def _rank(query, products, profile=None, learning_profile=None):
                return [{**product, "ranking": {"score": 0.8}} for product in products]

            tools_mod.get_product_catalog = _catalog
            tools_mod.rank_products = _rank
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            observation = execute_tool(
                "search_catalog",
                {"query": "show me what products you have", "profile": {}, "learning": {}},
                state={},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertIn("Hoodie", observation.message)
            self.assertTrue(observation.data.get("products"))
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome

    def test_deterministic_fallback_planner_maps_browse_to_catalog(self) -> None:
        decision = decide_action_without_llm("browse", {}, "show me products", state={"lastLanguage": "english"}, analysis={})
        self.assertEqual(decision.action, "search_catalog")

    def test_route_by_intent_maps_show_products(self) -> None:
        action = route_by_intent("browse", "show me products", {"workflow": {}, "cart": {}, "analysis": {}})
        self.assertEqual(action, "search_catalog")

    def test_fallback_by_intent_maps_order_status(self) -> None:
        self.assertEqual(fallback_by_intent("order_status"), "get_order_status")

    def test_general_and_browse_turns_are_cheap_when_safe(self) -> None:
        state = {"workflow": {}, "goal_tracking": {}}
        analysis = analyze_message("What do you have?", user={"role": "customer"}, state=state, learning_profile={})
        self.assertTrue(cheap_action_allowed(analysis, {"role": "customer"}, state))
        self.assertEqual(select_cost_tier(analysis, state, {"role": "customer"}), "cheap")
        self.assertLess(analysis["confidence"], 0.9)
        self.assertGreater(analysis["uncertainty"], 0.1)

    def test_manglish_browse_maps_to_browse(self) -> None:
        analysis = analyze_message("Eth products aan ullath?", user={"role": "customer"}, state={"workflow": {}}, learning_profile={})
        self.assertEqual(analysis["intent"], "browse")

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

    def test_non_useful_tool_response_does_not_stop_loop(self) -> None:
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
            self.assertNotEqual(result.reply, "Tell me what should be fixed in the order.")
            self.assertGreater(call_count["value"], 1)
        finally:
            main_mod.choose_next_action = original_choose_next_action
            main_mod.execute_tool = original_execute_tool

    def test_cart_text_quantity_updates_cart(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None, "description": "Durable boots"}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.9}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "I want 2 boots"},
                state={"cart": {"items": [], "address": ""}, "workflow": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"][0]["name"], "boots")
            self.assertEqual(observation.data["items"][0]["qty"], 2)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_multiple_sequential_item_additions_accumulate(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [
                {"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None},
                {"name": "hoodie", "display_name": "Hoodie", "price": 500, "offer_price": None},
            ]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": next((row for row in catalog if row["name"] == name.lower().strip()), None)
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.9 if product["name"] in query.lower() else 0.3}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            state = {"cart": {"items": [], "address": ""}, "workflow": {}}
            first = execute_tool("manage_cart", {"query": "2 boots"}, state=state, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            state["cart"] = first.state_patch["cart"]
            state["workflow"] = first.state_patch["workflow"]
            second = execute_tool("manage_cart", {"query": "hoodie x 3"}, state=state, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            self.assertTrue(second.ok)
            names = {item["name"]: item["qty"] for item in second.data["items"]}
            self.assertEqual(names["boots"], 2)
            self.assertEqual(names["hoodie"], 3)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_add_more_keyword_increments_existing_quantity(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "hoodies", "display_name": "Hoodies", "price": 500, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() in {"hoodie", "hoodies"} else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool("manage_cart", {"query": "add 3 more hoodies"}, state={"cart": {"items": [{"name": "hoodies", "qty": 2}], "address": ""}, "workflow": {}}, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"][0]["qty"], 5)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_remove_quantity_reduces_cart_deterministically(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "remove 1 boots"},
                state={"cart": {"items": [{"name": "boots", "qty": 7}], "address": ""}, "workflow": {}, "cart_meta": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"][0]["qty"], 6)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_remove_without_quantity_clears_item(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "remove boots"},
                state={"cart": {"items": [{"name": "boots", "qty": 7}], "address": ""}, "workflow": {}, "cart_meta": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"], [])
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_zero_quantity_sets_item_to_removed(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "0 boots"},
                state={"cart": {"items": [{"name": "boots", "qty": 6}], "address": ""}, "workflow": {}, "cart_meta": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"], [])
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_clear_cart_empties_all_items(self) -> None:
        import app.services.agent.tools as tools_mod

        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        try:
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "clear cart"},
                state={"cart": {"items": [{"name": "boots", "qty": 6}, {"name": "hoodie", "qty": 1}], "address": ""}, "workflow": {}, "cart_meta": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"], [])
        finally:
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome

    def test_repeated_message_id_is_idempotent(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            state = {"cart": {"items": [], "address": ""}, "workflow": {}, "cart_meta": {}}
            first = execute_tool(
                "manage_cart",
                {"query": "give me 7 boots", "message_id": "msg-1"},
                state=state,
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            state["cart"] = first.state_patch["cart"]
            state["workflow"] = first.state_patch["workflow"]
            state["cart_meta"] = first.state_patch["cart_meta"]
            second = execute_tool(
                "manage_cart",
                {"query": "give me 7 boots", "message_id": "msg-1"},
                state=state,
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(second.ok)
            self.assertTrue(second.data.get("idempotent_skip"))
            self.assertEqual(second.data["items"][0]["qty"], 7)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_partial_cart_failure_keeps_valid_item(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.9 if product["name"] in query.lower() else 0.1}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool("manage_cart", {"query": "2 boots and xyz123"}, state={"cart": {"items": [], "address": ""}, "workflow": {}}, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"][0]["name"], "boots")
            self.assertIn("xyz123", observation.message.lower())
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_json_cart_input_updates_cart(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [{"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None}]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": catalog[0] if name.lower().strip() == "boots" else None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.9}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool(
                "manage_cart",
                {"query": "{\"items\": [{\"product_name\": \"boots\", \"quantity\": 2}]}"},
                state={"cart": {"items": [], "address": ""}, "workflow": {}},
                user={"phone": "1", "role": "customer", "business_id": "biz_default"},
                style="english",
                memory={},
            )
            self.assertTrue(observation.ok)
            self.assertEqual(observation.data["items"][0]["qty"], 2)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_show_my_cart_routes_to_cart_view(self) -> None:
        state = dict(DEFAULT_STATE)
        state["cart"] = {"items": [{"name": "boots", "qty": 2}], "address": ""}
        state["workflow"] = {"name": "order_checkout", "stage": "collect_address"}
        analysis = analyze_message("show my cart", user={"role": "customer"}, state=state, learning_profile={})
        decision = choose_next_action(
            user_message="show my cart",
            user={"role": "customer", "phone": "1", "business_id": "biz_default"},
            state=state,
            memory_payload={"style": "english"},
            analysis=analysis,
            strategy={"mode": "order", "llm_tier": "balanced"},
            learning_profile={},
            scratchpad=[],
            max_steps=5,
        )
        self.assertEqual(decision.action, "manage_cart")
        self.assertEqual(decision.arguments.get("operation"), "view")

    def test_workflow_lock_persists_across_turns(self) -> None:
        state = dict(DEFAULT_STATE)
        state["cart"] = {"items": [{"name": "boots", "qty": 2}], "address": ""}
        state["workflow"] = {"name": "order_checkout", "stage": "collect_address"}
        analysis = analyze_message("I want 3 hoodies", user={"role": "customer"}, state=state, learning_profile={})
        decision = choose_next_action(
            user_message="I want 3 hoodies",
            user={"role": "customer", "phone": "1", "business_id": "biz_default"},
            state=state,
            memory_payload={"style": "english"},
            analysis=analysis,
            strategy={"mode": "order", "llm_tier": "balanced"},
            learning_profile={},
            scratchpad=[],
            max_steps=5,
        )
        self.assertEqual(decision.action, "manage_cart")

    def test_workflow_interrupts_on_product_browse(self) -> None:
        self.assertTrue(should_interrupt_workflow({"name": "order_checkout", "stage": "collect_address"}, "browse", "show me products"))

    def test_choose_next_action_uses_safety_route_before_planner(self) -> None:
        import app.services.agent.planner as planner_mod

        original_call_agent_llm = planner_mod.call_agent_llm
        try:
            def _raise(*args, **kwargs):
                raise AssertionError("LLM planner should not run for safety-routed browse")

            planner_mod.call_agent_llm = _raise
            decision = choose_next_action(
                user_message="show me products",
                user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                state=dict(DEFAULT_STATE),
                memory_payload={"style": "english"},
                analysis={"intent": "browse", "category": "sales", "uncertainty": 0.2, "needs_llm": True},
                strategy={"mode": "sales", "llm_tier": "balanced"},
                learning_profile={},
                scratchpad=[],
                max_steps=3,
            )
            self.assertEqual(decision.action, "search_catalog")
        finally:
            planner_mod.call_agent_llm = original_call_agent_llm

    def test_cheap_path_disabled_for_cart_extraction(self) -> None:
        state = {"workflow": {}, "goal_tracking": {}, "cart": {"items": [], "address": ""}}
        analysis = analyze_message("I want 2 boots", user={"role": "customer"}, state=state, learning_profile={})
        self.assertFalse(cheap_action_allowed(analysis, {"role": "customer"}, state))

    def test_cart_parser_keeps_quantities_separate(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [
                {"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None},
                {"name": "hoodies", "display_name": "Hoodies", "price": 500, "offer_price": None},
            ]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": next((row for row in catalog if row["name"] == name.lower().strip()), None)
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95 if product["name"] in query.lower() else 0.1}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            observation = execute_tool("manage_cart", {"query": "I want 2 boots and 3 hoodies"}, state={"cart": {"items": [], "address": ""}, "workflow": {}}, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            self.assertTrue(observation.ok)
            quantities = {item["name"]: item["qty"] for item in observation.data["items"]}
            self.assertEqual(quantities["boots"], 2)
            self.assertEqual(quantities["hoodies"], 3)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_followup_addition_does_not_modify_existing_product(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        original_feedback = tools_mod.record_recommendation_feedback
        try:
            catalog = [
                {"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None},
                {"name": "hoodies", "display_name": "Hoodies", "price": 500, "offer_price": None},
            ]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": next((row for row in catalog if row["name"] == name.lower().strip()), None)
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [{**product, "ranking": {"score": 0.95 if product["name"] in query.lower() else 0.1}} for product in products]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            tools_mod.record_recommendation_feedback = lambda *args, **kwargs: None
            state = {"cart": {"items": [{"name": "boots", "qty": 3}], "address": ""}, "workflow": {}, "cart_meta": {}}
            observation = execute_tool("manage_cart", {"query": "I also want 7 hoodies"}, state=state, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            quantities = {item["name"]: item["qty"] for item in observation.data["items"]}
            self.assertEqual(quantities["boots"], 3)
            self.assertEqual(quantities["hoodies"], 7)
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome
            tools_mod.record_recommendation_feedback = original_feedback

    def test_ambiguous_product_requests_clarification(self) -> None:
        import app.services.agent.tools as tools_mod

        original_catalog = tools_mod.get_product_catalog
        original_details = tools_mod.get_product_details
        original_rank = tools_mod.rank_products
        original_update_learning = tools_mod.update_learning_profile
        original_record_tool_outcome = tools_mod.record_tool_outcome
        try:
            catalog = [
                {"name": "boots", "display_name": "Boots", "price": 800, "offer_price": None},
                {"name": "boots pro", "display_name": "Boots Pro", "price": 1000, "offer_price": None},
            ]
            tools_mod.get_product_catalog = lambda **kwargs: catalog
            tools_mod.get_product_details = lambda name, business_id="biz_default": None
            tools_mod.rank_products = lambda query, products, profile=None, learning_profile=None: [
                {**products[0], "ranking": {"score": 0.74}},
                {**products[1], "ranking": {"score": 0.7}},
            ]
            tools_mod.update_learning_profile = lambda *args, **kwargs: None
            tools_mod.record_tool_outcome = lambda *args, **kwargs: None
            observation = execute_tool("manage_cart", {"query": "add 2 boot"}, state={"cart": {"items": [], "address": ""}, "workflow": {}, "cart_meta": {}}, user={"phone": "1", "role": "customer", "business_id": "biz_default"}, style="english", memory={})
            self.assertFalse(observation.ok)
            self.assertIn("clarify", observation.error.lower())
        finally:
            tools_mod.get_product_catalog = original_catalog
            tools_mod.get_product_details = original_details
            tools_mod.rank_products = original_rank
            tools_mod.update_learning_profile = original_update_learning
            tools_mod.record_tool_outcome = original_record_tool_outcome

    def test_address_with_house_and_po_is_accepted(self) -> None:
        import app.services.agent.tools as tools_mod

        observation = tools_mod._tool_collect_address(
            {"address": "K.T House, Iriya P.O, Near Temple, 671531"},
            {"cart": {"items": [], "address": ""}},
            {"phone": "1", "role": "customer", "business_id": "biz_default"},
            "english",
        )
        self.assertTrue(observation.ok)

    def test_invalid_address_requests_more_details(self) -> None:
        import app.services.agent.tools as tools_mod

        observation = tools_mod._tool_collect_address(
            {"address": "wait"},
            {"cart": {"items": [], "address": ""}},
            {"phone": "1", "role": "customer", "business_id": "biz_default"},
            "english",
        )
        self.assertFalse(observation.ok)
        self.assertIn("address", observation.error.lower())

    def test_valid_address_with_items_returns_preview(self) -> None:
        import app.services.agent.tools as tools_mod

        original_preview = tools_mod.build_order_preview
        try:
            tools_mod.build_order_preview = lambda items, address, style="english", business_id="biz_default": {"message": f"Preview for {address}"}
            observation = tools_mod._tool_collect_address(
                {"address": "K.T House, Iriya P.O, Near Temple, 671531"},
                {"cart": {"items": [{"name": "boots", "qty": 1}], "address": ""}},
                {"phone": "1", "role": "customer", "business_id": "biz_default"},
                "english",
            )
            self.assertTrue(observation.ok)
            self.assertIn("Preview for", observation.message)
            self.assertEqual(observation.data["normalized_address"]["address_line"], "K.T House")
            self.assertEqual(observation.data["normalized_address"]["pincode"], "671531")
        finally:
            tools_mod.build_order_preview = original_preview

    def test_i_want_to_see_my_order_maps_to_order_status(self) -> None:
        analysis = analyze_message("I want to see my order", user={"role": "customer"}, state={"workflow": {}, "cart": {"items": []}}, learning_profile={})
        self.assertEqual(analysis["intent"], "order_status")

    def test_support_issue_maps_to_support_intent(self) -> None:
        analysis = analyze_message("My order is late and the item is damaged, I need help", user={"role": "customer"}, state={"workflow": {}, "cart": {"items": []}}, learning_profile={})
        self.assertEqual(analysis["intent"], "support")

    def test_support_strategy_disables_upsell_and_prioritizes_recovery(self) -> None:
        analysis = {"intent": "support", "category": "support", "complexity": "multi_step"}
        strategy = build_strategy(
            user={"role": "customer"},
            profile={"segment": "vip"},
            analysis=analysis,
            state={"open_ticket_id": "ticket_1", "cart": {"items": []}, "goal_tracking": {}},
        )
        self.assertFalse(strategy["upsell_allowed"])
        self.assertTrue(strategy["support_guard"])
        self.assertGreater(strategy["support_recovery_priority"], 0.8)

    def test_browse_strategy_uses_customer_context_and_recommendations(self) -> None:
        import app.services.agent.tools as tools_mod

        original_stats = tools_mod.get_tool_policy_stats
        try:
            tools_mod.get_tool_policy_stats = lambda business_id="biz_default": {}
            analysis = {"intent": "browse", "category": "sales", "complexity": "normal", "uncertainty": 0.22}
            strategy = build_strategy(
                user={"role": "customer"},
                profile={"segment": "repeat"},
                analysis=analysis,
                state={"cart": {"items": []}, "goal_tracking": {}},
            )
            ranked = rank_tools(
                user={"role": "customer", "business_id": "biz_default"},
                state={"cart": {"items": []}, "workflow": {}, "goal_tracking": {}},
                analysis=analysis,
                strategy=strategy,
                learning_profile={},
                scratchpad=[],
            )
            top_names = [row["name"] for row in ranked[:6]]
            self.assertIn("inspect_customer_context", top_names)
            self.assertIn("recommend_products", top_names)
        finally:
            tools_mod.get_tool_policy_stats = original_stats

    def test_support_strategy_prioritizes_support_context_tools(self) -> None:
        import app.services.agent.tools as tools_mod

        original_stats = tools_mod.get_tool_policy_stats
        try:
            tools_mod.get_tool_policy_stats = lambda business_id="biz_default": {}
            analysis = {"intent": "support", "category": "support", "uncertainty": 0.31}
            strategy = build_strategy(
                user={"role": "customer"},
                profile={"segment": "new"},
                analysis=analysis,
                state={"open_ticket_id": "ticket_1", "cart": {"items": []}, "goal_tracking": {}, "feedback": {"sentiment": "negative"}},
            )
            ranked = rank_tools(
                user={"role": "customer", "business_id": "biz_default"},
                state={"open_ticket_id": "ticket_1", "cart": {"items": []}, "workflow": {}, "goal_tracking": {}, "feedback": {"sentiment": "negative"}},
                analysis=analysis,
                strategy=strategy,
                learning_profile={},
                scratchpad=[],
            )
            top_names = [row["name"] for row in ranked[:5]]
            self.assertIn("inspect_support_context", top_names)
            self.assertIn("create_support_ticket", top_names)
        finally:
            tools_mod.get_tool_policy_stats = original_stats

    def test_repeated_collect_address_failure_stops_with_strict_fallback(self) -> None:
        import app.services.agent.main_agent as main_mod

        original_choose_next_action = main_mod.choose_next_action
        original_execute_tool = main_mod.execute_tool
        try:
            call_count = {"value": 0}

            def _decision(**kwargs):
                call_count["value"] += 1
                if call_count["value"] <= 2:
                    return PlannerDecision(action="collect_address", arguments={"address": "no", "raw_message": kwargs["user_message"]}, confidence=0.8)
                return PlannerDecision(action="manage_cart", arguments={"query": kwargs["user_message"], "raw_message": kwargs["user_message"]}, confidence=0.7)

            def _tool(action, arguments, *args, **kwargs):
                if action == "collect_address":
                    return ToolObservation(tool="collect_address", ok=False, message="", error="invalid address")
                return ToolObservation(tool="manage_cart", ok=True, message="cart updated", data={"items": [{"name": "boots", "qty": 1}]})

            main_mod.choose_next_action = _decision
            main_mod.execute_tool = _tool
            result = asyncio.run(
                main_mod.run_agent(
                    user_message="I want 1 boots",
                    user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                    state={"workflow": {"name": "order_checkout", "stage": "collect_address"}, "cart": {"items": [{"name": "tea", "qty": 1}], "address": ""}, "goal_tracking": {}, "tool_failures": {}},
                    memory_payload={"style": "english"},
                    profile={},
                    learning_profile={},
                    style="english",
                )
            )
            self.assertIn("full address", result.reply.lower())
            self.assertGreaterEqual(result.updated_state.get("tool_failures", {}).get("collect_address", 0), 1)
        finally:
            main_mod.choose_next_action = original_choose_next_action
            main_mod.execute_tool = original_execute_tool

    def test_llm_provider_failure_falls_back_to_gemini_same_call(self) -> None:
        import app.services.llm.llm_router as router_mod
        from app.core.settings import get_settings

        with patch.dict("os.environ", {"OPENAI_API_KEY": "x", "GEMINI_API_KEY": "y", "LLM_PROVIDER_PRIORITY": "openai,gemini"}, clear=False):
            get_settings.cache_clear()
            with patch.object(router_mod, "call_openai_chat", side_effect=RuntimeError("quota")) as openai_call:
                with patch.object(router_mod, "call_gemini_chat", return_value={"provider": "gemini", "model": "gemini-2.0-flash", "content": "fallback ok"}) as gemini_call:
                    reply = router_mod.call_llm([{"role": "user", "content": "hi"}], json_mode=False, tier="balanced")
                    self.assertEqual(reply, "fallback ok")
                    self.assertEqual(openai_call.call_count, 1)
                    self.assertEqual(gemini_call.call_count, 1)

    def test_vendor_notification_failure_is_graceful(self) -> None:
        import app.services.vendor as vendor_mod

        original_send = vendor_mod.send_whatsapp_message
        original_queue = vendor_mod.queue_vendor_notification
        original_contact = vendor_mod._vendor_contact_phone
        original_profile = vendor_mod.get_business_profile
        try:
            queued = {"value": False}

            def _raise(*args, **kwargs):
                raise RuntimeError("Twilio message send failed: 429 rate limit")

            vendor_mod.send_whatsapp_message = _raise
            vendor_mod.queue_vendor_notification = lambda order, business_id="biz_default", error="": queued.__setitem__("value", True)
            vendor_mod._vendor_contact_phone = lambda business_id="biz_default": "whatsapp:+911234567890"
            vendor_mod.get_business_profile = lambda business_id="biz_default": {"whatsapp_number": "whatsapp:+911234567891"}
            order = SimpleNamespace(
                orderId="ord_1",
                address="K.T House, Iriya P.O",
                customerPhone="whatsapp:+911234567890",
                total=100.0,
                items=[SimpleNamespace(name="boots", qty=1)],
                model_dump_json=lambda indent=2: "{}",
                model_dump=lambda: {},
            )
            result = vendor_mod.send_vendor_order_notification(order, business_id="biz_default")
            self.assertFalse(result)
            self.assertTrue(queued["value"])
        finally:
            vendor_mod.send_whatsapp_message = original_send
            vendor_mod.queue_vendor_notification = original_queue
            vendor_mod._vendor_contact_phone = original_contact
            vendor_mod.get_business_profile = original_profile

    def test_successful_tool_result_stops_agent_loop(self) -> None:
        import app.services.agent.main_agent as main_mod

        original_choose_next_action = main_mod.choose_next_action
        original_execute_tool = main_mod.execute_tool
        try:
            calls = {"planner": 0}

            def _decision(**kwargs):
                calls["planner"] += 1
                return PlannerDecision(action="collect_address", arguments={"address": "K.T House, Iriya P.O"}, confidence=0.9)

            def _tool(*args, **kwargs):
                return ToolObservation(tool="collect_address", ok=True, message="Preview ready", data={"address": "K.T House, Iriya P.O"}, state_patch={"workflow": {"stage": "ready_to_checkout"}})

            main_mod.choose_next_action = _decision
            main_mod.execute_tool = _tool
            result = asyncio.run(
                main_mod.run_agent(
                    user_message="My address is K.T House, Iriya P.O",
                    user={"role": "customer", "phone": "1", "business_id": "biz_default"},
                    state={"workflow": {"name": "order_checkout", "stage": "collect_address"}, "cart": {"items": [{"name": "boots", "qty": 1}], "address": ""}, "goal_tracking": {}, "tool_failures": {}},
                    memory_payload={"style": "english"},
                    profile={},
                    learning_profile={},
                    style="english",
                )
            )
            self.assertEqual(result.reply, "Preview ready")
            self.assertEqual(calls["planner"], 1)
        finally:
            main_mod.choose_next_action = original_choose_next_action
            main_mod.execute_tool = original_execute_tool


if __name__ == "__main__":
    unittest.main()

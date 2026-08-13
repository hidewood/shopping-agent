from __future__ import annotations

import sys
import unittest
from uuid import uuid4
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    BundleLineItem,
    BundlePurchaseContext,
    CatalogConstraint,
    ConversationState,
    ModelObservability,
    PriceConstraint,
    ProductRepository,
    ShoppingRequirement,
    ShoppingAgent,
)
from tests.test_product_repository import FailingLLM, TestLLM


DATA_DIR = PROJECT_DIR / "data"


def selection_plan(requirement: dict, *, state_action: str = "merge") -> dict:
    return {
        "goal": "selection",
        "target": "catalog",
        "customer_reply": None,
        "requirement": requirement,
        "catalog_operations": [],
        "state_action": state_action,
        "selection_mode": "criteria",
        "action": None,
        "goal_evidence": [],
    }


class StaticPlanLLM:
    def __init__(self, plan: dict):
        self.plan = plan
        self.calls = 0

    def chat_json(self, _messages: list[dict]) -> dict:
        self.calls += 1
        return self.plan


class ResilientContinuationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ProductRepository(DATA_DIR)
        self.agent = ShoppingAgent(DATA_DIR)

    def test_capability_overview_never_claims_unsupported_transactions(self) -> None:
        """A1: a broad greeting must describe the actual capability boundary."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("你好，你能做什么？", ConversationState())

        self.assertEqual(result["response_type"], "chat")
        self.assertIn("查询商品、比较商品", result["summary"])
        self.assertIn("不支持下单、支付或取消订单", result["summary"])
        self.assertTrue(
            any(step["step"] == "deterministic_capability_overview" for step in result["trace"])
        )
        self.assertFalse(any(step["step"] == "model_service" for step in result["trace"]))

    def test_bare_greeting_has_a_deterministic_fallback(self) -> None:
        """A basic greeting must remain usable when the model service is unavailable."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("你好", ConversationState())

        self.assertEqual(result["response_type"], "chat")
        self.assertIn("您好", result["summary"])
        self.assertTrue(
            any(step["step"] == "deterministic_greeting_fallback" for step in result["trace"])
        )
        self.assertTrue(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_favorites_and_simulated_orders_are_local_and_do_not_call_the_model(self) -> None:
        self.agent.llm = FailingLLM()
        state = ConversationState()

        favorite = self.agent.run_turn("收藏 P0005", state)
        self.assertEqual(favorite["response_type"], "local_collection")
        self.assertEqual(state.saved_product_ids, ["P0005"])
        self.assertFalse(any(step["step"] == "turn_planning" for step in favorite["trace"]))

        created = self.agent.run_turn("创建模拟订单 P0005", state)
        self.assertEqual(created["response_type"], "local_collection")
        self.assertEqual(created["catalog_data"]["order"]["order_id"], "SIM-0001")
        self.assertEqual(created["catalog_data"]["order"]["status"], "confirmed_local")
        self.assertIn("不会发起真实下单或支付", created["summary"])

        listed = self.agent.run_turn("查看模拟订单", state)
        self.assertEqual(listed["catalog_data"]["kind"], "simulated_order_list")
        self.assertEqual(listed["catalog_data"]["orders"][0]["order_id"], "SIM-0001")

        cancelled = self.agent.run_turn("取消模拟订单 SIM-0001", state)
        self.assertEqual(cancelled["catalog_data"]["order"]["status"], "cancelled_local")
        self.assertEqual(state.simulated_orders[0]["status"], "cancelled_local")

    def test_local_mock_order_follow_up_and_repeat_cancellation_are_stateful(self) -> None:
        """An omitted local order ID is a resumable local slot, not an LLM turn."""
        self.agent.llm = FailingLLM()
        state = ConversationState()
        self.agent.run_turn("创建模拟订单 P0005", state)

        question = self.agent.run_turn("取消模拟订单", state)
        self.assertEqual(question["response_type"], "clarification")
        self.assertEqual(state.pending_local_action, "cancel_simulated_order")
        self.assertEqual(state.pending_fields, ["simulated_order_id"])

        cancelled = self.agent.run_turn("SIM-0001", state)
        self.assertEqual(cancelled["response_type"], "local_collection")
        self.assertEqual(cancelled["catalog_data"]["order"]["status"], "cancelled_local")
        self.assertIsNone(state.pending_local_action)
        self.assertFalse(any(step["step"] == "turn_planning" for step in cancelled["trace"]))

        repeated = self.agent.run_turn("取消模拟订单 SIM-0001", state)
        self.assertIn("已处于取消状态", repeated["summary"])
        self.assertTrue(any(step["status"] == "already_cancelled" for step in repeated["trace"]))

    def test_local_mock_order_keeps_every_explicit_product_id(self) -> None:
        self.agent.llm = FailingLLM()
        state = ConversationState()

        created = self.agent.run_turn("创建模拟订单 P0005 和 P0011", state)
        self.assertEqual(created["response_type"], "local_collection")
        order = created["catalog_data"]["order"]
        self.assertEqual(order["product_ids"], ["P0005", "P0011"])
        self.assertEqual(order["line_items"], [
            {"product_id": "P0005", "quantity": 1},
            {"product_id": "P0011", "quantity": 1},
        ])
        self.assertEqual(order["total_price"], 19.98)

    def test_bundle_lines_keep_their_own_theme_constraints(self) -> None:
        """A theme before each type filters that type only, without model planning."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "我想买 Ocean主题马克杯和 Snow主题T恤，每件预算20元以内，请给我组合方案。",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        items = result["catalog_data"]["bundle"]["items"]
        self.assertEqual(items[0]["line_requirement"]["concepts"][0]["raw_value"], "Ocean")
        self.assertEqual(items[1]["line_requirement"]["concepts"][0]["raw_value"], "Snow")
        self.assertIn("Ocean", items[0]["product"]["tags"])
        self.assertIn("Snow", items[1]["product"]["tags"])
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_bundle_quantity_is_counted_in_total_budget_verification(self) -> None:
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "我想买2个马克杯和1件T恤，总预算30元以内，请给我组合方案。",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        items = result["catalog_data"]["bundle"]["items"]
        self.assertEqual([item["quantity"] for item in items], [2, 1])
        expected_total = round(sum(item["quantity"] * item["product"]["price"] for item in items), 2)
        self.assertEqual(result["catalog_data"]["bundle"]["total_price"], expected_total)
        self.assertLessEqual(expected_total, 30)

    def test_excessive_bundle_quantity_is_clarified_instead_of_being_silently_changed(self) -> None:
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "我想买11个马克杯和1件T恤，总预算300元以内，请给我组合方案。",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "clarification")
        self.assertIn("每类商品最多支持 10 件", result["summary"])
        validation = next(step for step in result["trace"] if step["step"] == "bundle_quantity_validation")
        self.assertEqual(validation["invalid_line_items"], [{"item_type": "mug", "quantity": 11}])
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_bundle_follow_up_favorite_requires_an_explicit_item_id(self) -> None:
        self.agent.llm = FailingLLM()
        state = ConversationState()
        self.agent.run_turn(
            "我想买一套马克杯和T恤，总预算20元以内，请给我组合方案。", state
        )

        clarification = self.agent.run_turn("收藏它", state)
        self.assertEqual(clarification["response_type"], "clarification")
        self.assertIn("上一轮组合", clarification["summary"])
        self.assertEqual(state.saved_product_ids, [])

        saved = self.agent.run_turn("P0005", state)
        self.assertEqual(saved["response_type"], "local_collection")
        self.assertEqual(state.saved_product_ids, ["P0005"])

    def test_rejected_real_order_preserves_the_current_selection_context(self) -> None:
        """Capability boundaries must not erase a verified product selection."""
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        selection = self.agent.run_turn("我想买 Ocean主题马克杯，预算20元以内", state)
        self.assertEqual(selection["response_type"], "recommendation")
        self.assertEqual(state.task_context.active_task, "selection")

        self.agent.llm = StaticPlanLLM(
            {
                "goal": "action",
                "target": "transaction",
                "customer_reply": None,
                "requirement": None,
                "catalog_operations": [],
                "state_action": "none",
                "selection_mode": None,
                "action": "order.create",
                "goal_evidence": ["下单"],
            }
        )
        rejected = self.agent.run_turn("帮我下单 P0005", state)

        self.assertEqual(rejected["response_type"], "capability_unavailable")
        self.assertEqual(state.task_context.active_task, "selection")
        self.assertEqual(state.task_context.selected_product_id, selection["purchased_product_id"])
        self.assertTrue(
            any(
                step["step"] == "task_state" and step.get("selection_preserved")
                for step in rejected["trace"]
            )
        )

    def test_chinese_english_tag_followed_by_theme_is_a_hard_constraint(self) -> None:
        """A2: ``Ocean主题`` must not depend on the default ranking coincidentally."""
        state = ConversationState()
        self.agent.llm = StaticPlanLLM(
            selection_plan(
                {
                    "item_type": {
                        "raw_value": "mug",
                        "constraint_strength": "hard",
                        "catalog_hint": "mug",
                    },
                    "manufacturer": None,
                    "price_constraint": None,
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            )
        )
        self.agent.run_turn("我想买一个马克杯", state)

        self.agent.llm = StaticPlanLLM(
            selection_plan(
                {
                    "item_type": None,
                    "manufacturer": None,
                    "price_constraint": {"operator": "<=", "value": 20},
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            )
        )
        result = self.agent.run_turn("Ocean主题，预算20元以内", state)

        self.assertEqual(result["response_type"], "recommendation")
        grounded = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertEqual(grounded["grounded_requirements"]["required_tags"], ["Ocean"])
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertIn("Ocean", selected.tags)

    def test_no_match_price_advice_states_the_actual_relaxation(self) -> None:
        """A15: a nearest price alternative cannot repeat the rejected budget."""
        self.agent.llm = StaticPlanLLM(
            selection_plan(
                {
                    "item_type": {
                        "raw_value": "mug",
                        "constraint_strength": "hard",
                        "catalog_hint": "mug",
                    },
                    "manufacturer": None,
                    "price_constraint": {"operator": "<=", "value": 8},
                    "concepts": [
                        {
                            "raw_value": "Ocean",
                            "kind": "theme",
                            "constraint_strength": "hard",
                            "catalog_tag_hints": ["Ocean"],
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            )
        )

        result = self.agent.run_turn("我想要 Ocean主题马克杯，预算8元以内", ConversationState())

        self.assertEqual(result["response_type"], "no_match")
        self.assertIn("只差 $1.99", result["summary"])
        self.assertIn("要不要看看", result["summary"])
        self.assertNotIn("若放宽价格条件（", result["summary"])

    def test_explicit_open_request_recommends_without_waiting_for_the_planner(self) -> None:
        """A17: an explicit request for any shirt should skip type-only exploration."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("我想买一件T恤，不限预算和风格，直接推荐一个", ConversationState())

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertEqual(selected.item_type, "shirt")
        self.assertTrue(
            any(step["step"] == "explicit_open_recommendation" for step in result["trace"])
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_multi_item_purchase_is_clarified_before_planner_execution(self) -> None:
        """C10: a mug-and-shirt request must not fail on an unrelated model plan."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("我想买马克杯和T恤，预算20以内", ConversationState())

        self.assertEqual(result["response_type"], "conflict")
        self.assertIn("同时购买 mug、shirt", result["summary"])
        self.assertIn("每件商品的上限", result["summary"])
        self.assertIn("合计预算", result["summary"])
        self.assertEqual(
            result["conversation_state"]["pending_fields"], ["item_type", "budget_scope"]
        )
        self.assertTrue(
            any(
                step["step"] == "bundle_purchase_detection"
                and step["item_types"] == ["mug", "shirt"]
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_bundle_type_answer_still_requires_budget_scope_before_recommendation(self) -> None:
        """Choosing the first item must not silently turn a shared budget into a per-item limit."""
        self.agent.llm = FailingLLM()
        state = ConversationState()

        self.agent.run_turn("我想买马克杯和T恤，预算20以内", state)
        type_answer = self.agent.run_turn("先买马克杯吧", state)

        self.assertEqual(type_answer["response_type"], "conflict")
        self.assertIsNone(type_answer["purchased_product_id"])
        self.assertEqual(type_answer["conversation_state"]["pending_fields"], ["budget_scope"])
        self.assertEqual(
            type_answer["conversation_state"]["bundle_context"]["selected_item_type"], "mug"
        )
        self.assertIn("每件商品的上限", type_answer["summary"])
        self.assertFalse(any(step["step"] == "turn_planning" for step in type_answer["trace"]))

        per_item_answer = self.agent.run_turn("每件商品预算20元以内", state)

        self.assertEqual(per_item_answer["response_type"], "recommendation")
        selected = self.repository.by_id[per_item_answer["purchased_product_id"]]
        self.assertEqual(selected.item_type, "mug")
        self.assertLessEqual(selected.price, 20)
        self.assertIsNone(per_item_answer["conversation_state"]["bundle_context"])
        self.assertTrue(
            any(
                step["step"] == "bundle_purchase_subtask"
                and step["budget_scope"] == "per_item"
                for step in per_item_answer["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in per_item_answer["trace"]))

    def test_bundle_combined_budget_requires_an_item_allocation(self) -> None:
        """A combined limit needs an explicit item-level allocation before retrieval."""
        self.agent.llm = FailingLLM()
        state = ConversationState()

        self.agent.run_turn("I need a mug and a shirt under $20.", state)
        combined_answer = self.agent.run_turn("Mug first; total budget.", state)

        self.assertEqual(combined_answer["response_type"], "conflict")
        self.assertIsNone(combined_answer["purchased_product_id"])
        self.assertEqual(
            combined_answer["conversation_state"]["pending_fields"], ["item_price_constraint"]
        )
        self.assertIn("不会自动拆分合计预算", combined_answer["summary"])

        allocation_answer = self.agent.run_turn("The mug should be under $12.", state)

        self.assertEqual(allocation_answer["response_type"], "recommendation")
        selected = self.repository.by_id[allocation_answer["purchased_product_id"]]
        self.assertEqual(selected.item_type, "mug")
        self.assertLess(selected.price, 12)
        self.assertTrue(
            any(
                step["step"] == "bundle_purchase_subtask"
                and step["source"] == "combined_budget_item_allocation"
                for step in allocation_answer["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in allocation_answer["trace"]))

    def test_chinese_explicit_per_item_bundle_returns_one_verified_item_per_type(self) -> None:
        """An explicit bundle request may safely bypass the single-item subtask."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "我想买一套马克杯和T恤，每件预算20元以内，请给我组合方案。",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        bundle = result["catalog_data"]["bundle"]
        products = result["catalog_data"]["products"]
        self.assertEqual([item["item_type"] for item in products], ["mug", "shirt"])
        self.assertEqual(bundle["item_types"], ["mug", "shirt"])
        self.assertTrue(all(item["price"] <= 20 for item in products))
        self.assertEqual(bundle["total_price"], round(sum(item["price"] for item in products), 2))
        self.assertIsNone(result["conversation_state"]["bundle_context"])
        self.assertTrue(
            any(
                step["step"] == "bundle_decision"
                and step["source"] == "direct_per_item_bundle_request"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_explicit_combined_budget_bundle_is_verified_as_a_pair(self) -> None:
        """A stated total cap is checked against the pair, never auto-split."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "我想买一套马克杯和T恤，总预算20元以内，请给我组合方案。",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        bundle = result["catalog_data"]["bundle"]
        products = result["catalog_data"]["products"]
        self.assertEqual([item["item_type"] for item in products], ["mug", "shirt"])
        self.assertEqual(bundle["budget_scope"], "combined")
        self.assertLessEqual(bundle["total_price"], 20)
        self.assertEqual(bundle["total_price"], round(sum(item["price"] for item in products), 2))
        self.assertTrue(
            any(
                step["step"] == "bundle_combination_search"
                and step["status"] == "completed"
                and step["source"] == "direct_combined_budget_bundle_request"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_concise_combined_bundle_phrase_skips_general_planning(self) -> None:
        """A direct Chinese bundle phrase need not repeat a purchase verb."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "马克杯和T恤，总预算20以内，给我组合方案",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        bundle = result["catalog_data"]["bundle"]
        self.assertEqual(bundle["budget_scope"], "combined")
        self.assertLessEqual(bundle["total_price"], 20)
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_greeting_before_a_concise_combined_bundle_stays_in_bundle_route(self) -> None:
        """Earlier chat must not make a later two-item request look like one category."""
        self.agent.llm = TestLLM(self.repository)
        state = ConversationState()

        greeting = self.agent.run_turn("你好", state)
        self.assertEqual(greeting["response_type"], "chat")

        result = self.agent.run_turn("马克杯和T恤，总预算20以内，给我组合方案", state)

        self.assertEqual(result["response_type"], "bundle_recommendation")
        bundle = result["catalog_data"]["bundle"]
        self.assertEqual(bundle["item_types"], ["mug", "shirt"])
        self.assertEqual(bundle["budget_scope"], "combined")
        self.assertLessEqual(bundle["total_price"], 20)
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_explicit_combined_budget_bundle_reports_an_unreachable_total(self) -> None:
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "I need a mug and a shirt, total budget under $10; recommend a bundle.",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "no_match")
        self.assertIn("合计预算", result["summary"])
        self.assertEqual(result["catalog_data"]["bundle"]["budget_scope"], "combined")
        self.assertEqual(result["catalog_data"]["bundle"]["eligible_pair_count"], 0)

    def test_english_clarified_bundle_returns_one_verified_item_per_type(self) -> None:
        """The bundle branch also works after an English multi-turn clarification."""
        self.agent.llm = FailingLLM()
        state = ConversationState()

        self.agent.run_turn("I need a mug and a shirt under $20.", state)
        result = self.agent.run_turn("Each item under $20; recommend a bundle.", state)

        self.assertEqual(result["response_type"], "bundle_recommendation")
        products = result["catalog_data"]["products"]
        self.assertEqual({item["item_type"] for item in products}, {"mug", "shirt"})
        self.assertTrue(all(item["price"] <= 20 for item in products))
        self.assertEqual(result["catalog_data"]["bundle"]["total_price"], 19.98)
        self.assertTrue(
            any(
                step["step"] == "bundle_decision"
                and step["source"] == "clarified_per_item_bundle_request"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_multiple_types_in_a_catalog_question_do_not_trigger_bundle_clarification(self) -> None:
        """Multiple types without a purchase goal return separate verified ranges."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("mug 和 shirt 分别有什么价位？", ConversationState())

        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["kind"], "multi_type_price_range")
        ranges = result["catalog_data"]["price_ranges"]
        self.assertEqual([item["item_type"] for item in ranges], ["mug", "shirt"])
        for item in ranges:
            products = sorted(
                self.agent._products_of_type(item["item_type"]),
                key=lambda product: (product.price, product.product_id),
            )
            self.assertEqual(item["count"], len(products))
            self.assertEqual(item["lowest"]["product_id"], products[0].product_id)
            self.assertEqual(item["highest"]["product_id"], products[-1].product_id)
        self.assertFalse(
            any(step["step"] == "bundle_purchase_detection" for step in result["trace"])
        )
        self.assertTrue(
            any(step["step"] == "multi_type_price_range_query" for step in result["trace"])
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_english_plural_multi_type_price_query_preserves_active_selection(self) -> None:
        """A two-type fact question must remain read-only even during selection."""
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("I want an Ocean themed mug under $20.", state)
        before = self.agent._reduce_requirement(state).to_dict()

        self.agent.llm = FailingLLM()
        result = self.agent.run_turn("What price ranges do mugs and shirts have?", state)

        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["kind"], "multi_type_price_range")
        self.assertEqual(
            [item["item_type"] for item in result["catalog_data"]["price_ranges"]],
            ["mug", "shirt"],
        )
        task_state = next(step for step in result["trace"] if step["step"] == "task_state")
        self.assertEqual(task_state["task"], "selection")
        self.assertTrue(task_state["selection_preserved"])
        self.assertEqual(task_state["current"]["active_task"], "selection")
        self.assertEqual(self.agent._reduce_requirement(state).to_dict(), before)
        self.assertEqual(state.task_context.active_task, "selection")
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_type_replacement_ignores_the_withdrawn_type_when_checking_conflicts(self) -> None:
        """“不要杯子，改成 shirt” is one replacement request, not two types."""
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("I need a mug under $20.", state)

        self.agent.llm = StaticPlanLLM(
            selection_plan(
                {
                    "item_type": {
                        "raw_value": "shirt",
                        "constraint_strength": "hard",
                        "catalog_hint": "shirt",
                    },
                    "manufacturer": None,
                    "price_constraint": {"operator": "<", "value": 25},
                    "concepts": [
                        {
                            "raw_value": "Snow",
                            "kind": "theme",
                            "constraint_strength": "hard",
                            "catalog_tag_hints": ["Snow"],
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                }
            )
        )
        result = self.agent.run_turn(
            "不要杯子了，改成 Snow themed shirt under $25.", state
        )

        self.assertEqual(self.agent._mentioned_item_types("不要杯子了，改成 Snow themed shirt under $25."), ["shirt"])
        self.assertEqual(result["response_type"], "recommendation")
        self.assertFalse(any(step["step"] == "conflict_detection" for step in result["trace"]))
        self.assertEqual(self.repository.by_id[result["purchased_product_id"]].item_type, "shirt")
        self.assertEqual(self.agent._reduce_requirement(state).item_type.catalog_hint, "shirt")

    def test_type_agnostic_gift_request_clarifies_without_calling_an_unavailable_planner(self) -> None:
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("我想买一份礼物", ConversationState())

        self.assertEqual(result["response_type"], "clarification")
        self.assertEqual(result["conversation_state"]["pending_fields"], ["item_type"])
        self.assertTrue(
            any(
                step["step"] == "deterministic_clarification"
                and step["reason"] == "gift_without_item_type"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "model_service" for step in result["trace"]))

    def test_explicit_price_recommendation_continues_an_active_selection_without_planner(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("I want an Ocean themed mug under $25.", state)
        catalog_result = self.agent.run_turn("How many Ocean themed mugs are there?", state)
        self.assertEqual(catalog_result["response_type"], "catalog_query")
        self.assertEqual(state.task_context.active_task, "selection")

        self.agent.llm = FailingLLM()
        result = self.agent.run_turn("Recommend one under $20.", state)

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertEqual(selected.item_type, "mug")
        self.assertLess(selected.price, 20)
        active = self.agent._reduce_requirement(state)
        self.assertEqual(active.price_constraint.value, 20)
        self.assertTrue(
            any(step["step"] == "active_selection_price_refinement" for step in result["trace"])
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_generic_recommendation_reuses_active_selection_after_read_only_query(self) -> None:
        """A terse English direct-pick follow-up inherits verified active constraints."""
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("I want an Ocean themed mug under $20.", state)
        catalog_result = self.agent.run_turn("How many Ocean themed mugs are there?", state)
        self.assertEqual(catalog_result["response_type"], "catalog_query")

        self.agent.llm = FailingLLM()
        result = self.agent.run_turn("Recommend one.", state)

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertEqual(selected.item_type, "mug")
        self.assertIn("Ocean", selected.tags)
        self.assertLessEqual(selected.price, 20)
        self.assertTrue(
            any(
                step["step"] == "active_selection_generic_recommendation"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_chinese_generic_recommendation_reuses_active_selection_after_read_only_query(self) -> None:
        """The same deterministic continuation supports the Chinese direct-pick form."""
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("我想买 Ocean主题马克杯，预算20元以内", state)
        catalog_result = self.agent.run_turn("Ocean主题马克杯有多少件？", state)
        self.assertEqual(catalog_result["response_type"], "catalog_query")

        self.agent.llm = FailingLLM()
        result = self.agent.run_turn("给我推荐一个", state)

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertEqual(selected.item_type, "mug")
        self.assertIn("Ocean", selected.tags)
        self.assertLessEqual(selected.price, 20)
        self.assertTrue(
            any(
                step["step"] == "active_selection_generic_recommendation"
                for step in result["trace"]
            )
        )
        self.assertFalse(any(step["step"] == "turn_planning" for step in result["trace"]))

    def test_explicit_local_favorite_is_a_secondary_ranking_signal(self) -> None:
        """A saved item may break candidate ties but cannot replace hard filtering."""
        state = ConversationState()
        state.preference_profile.record_product(
            self.repository.by_id["P0011"], signal="favorite"
        )
        requirement = self.repository.ground(
            ShoppingRequirement(
                item_type=CatalogConstraint(
                    raw_value="mug", constraint_strength="hard", catalog_hint="mug"
                ),
                price_constraint=PriceConstraint(operator="<=", value=20),
            )
        )
        candidates, _ = self.repository.retrieve(requirement)
        trace: list[dict] = []

        _, selected = self.agent._rank_candidates(
            requirement, candidates, trace, state=state
        )

        self.assertEqual(selected.product_id, "P0011")
        comparison = next(step for step in trace if step["step"] == "candidate_comparison")
        self.assertTrue(comparison["session_profile_used"])
        self.assertGreater(comparison["selected_session_profile_score"]["total"], 0)

    def test_local_collections_and_profile_can_be_restored_on_this_device(self) -> None:
        """Only explicit local collections are persisted; chat history is not."""
        state_dir = PROJECT_DIR / "local_state" / f"test-{uuid4().hex}"
        writer = ShoppingAgent(DATA_DIR, local_state_dir=state_dir)
        writer.llm = FailingLLM()
        state = ConversationState(conversation_id="persisted-session-01")
        writer.run_turn("收藏 P0005", state)
        writer.run_turn("创建模拟订单 P0005 和 P0011", state)

        reader = ShoppingAgent(DATA_DIR, local_state_dir=state_dir)
        restored = ConversationState(conversation_id="persisted-session-01")
        status = reader.restore_local_session(restored)

        self.assertEqual(status["status"], "loaded")
        self.assertEqual(restored.saved_product_ids, ["P0005"])
        self.assertEqual(restored.simulated_orders[0]["product_ids"], ["P0005", "P0011"])
        self.assertTrue(restored.preference_profile.has_signal())
        self.assertEqual(restored.events, [])

    def test_combined_bundle_search_accepts_more_than_two_line_items(self) -> None:
        """The bundle solver works on line items, not a fixed mug/shirt pair."""
        state = ConversationState()
        context = BundlePurchaseContext(
            item_types=["mug", "shirt", "mug"],
            line_items=[
                BundleLineItem(item_type="mug"),
                BundleLineItem(item_type="shirt"),
                BundleLineItem(item_type="mug"),
            ],
            budget_scope="combined",
        )
        trace: list[dict] = []

        result = self.agent._handle_combined_budget_bundle_recommendation(
            state,
            "three local lines",
            context,
            PriceConstraint(operator="<=", value=30),
            trace,
            source="test",
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        items = result["catalog_data"]["bundle"]["items"]
        self.assertEqual(len(items), 3)
        self.assertLessEqual(result["catalog_data"]["bundle"]["total_price"], 30)
        search = next(step for step in trace if step["step"] == "bundle_combination_search")
        self.assertEqual(search["line_item_count"], 3)
        self.assertIn(search["search_strategy"], {"exact_enumeration", "bounded_beam"})

    def test_bundle_parser_keeps_repeated_types_as_independent_lines(self) -> None:
        """A line-item bundle may contain two differently scoped mugs."""
        line_items = self.agent._bundle_line_items_from_message(
            "Ocean mug and Beach mug plus Snow shirt", ["mug", "shirt"]
        )

        self.assertEqual([item.item_type for item in line_items], ["mug", "mug", "shirt"])
        self.assertEqual(
            [[concept.raw_value for concept in item.concepts] for item in line_items],
            [["Ocean"], ["Beach"], ["Snow"]],
        )

    def test_bundle_parser_keeps_a_quantity_before_a_scoped_type(self) -> None:
        line_items = self.agent._bundle_line_items_from_message(
            "2个 Ocean mug 和 1件 Snow shirt", ["mug", "shirt"]
        )

        self.assertEqual([item.quantity for item in line_items], [2, 1])

    def test_scoped_multi_quantity_bundle_is_executed_without_a_planner(self) -> None:
        """A numeric prefix before a themed item survives into bundle pricing."""
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn(
            "2个 Ocean mug 和 1件 Snow shirt，总预算30元以内，给我组合方案",
            ConversationState(),
        )

        self.assertEqual(result["response_type"], "bundle_recommendation")
        bundle = result["catalog_data"]["bundle"]
        self.assertEqual([item["quantity"] for item in bundle["items"]], [2, 1])
        self.assertLessEqual(bundle["total_price"], 30.0)

    def test_model_observability_records_failed_planning_without_storing_prompt_text(self) -> None:
        self.agent.llm = FailingLLM()

        result = self.agent.run_turn("帮我找一个有特色的马克杯", ConversationState())
        snapshot = self.agent.observability_snapshot()

        self.assertEqual(result["response_type"], "service_error")
        self.assertGreaterEqual(snapshot["calls"], 1)
        self.assertGreaterEqual(snapshot["failures"], 1)
        self.assertNotIn("帮我找一个有特色的马克杯", str(snapshot))

    def test_model_observability_uses_nearest_rank_p95_for_small_samples(self) -> None:
        metrics = ModelObservability()
        metrics.record_success(20)
        metrics.record_success(80)

        self.assertEqual(metrics.snapshot()["p95_latency_ms"], 80)


if __name__ == "__main__":
    unittest.main()

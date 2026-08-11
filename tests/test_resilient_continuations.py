from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import ConversationState, ProductRepository, ShoppingAgent
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
        self.assertIn("取消预算限制", result["summary"])
        self.assertIn("与原预算相差 $1.99", result["summary"])
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
        self.assertIn("同时购买 mug 和 shirt", result["summary"])
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


if __name__ == "__main__":
    unittest.main()

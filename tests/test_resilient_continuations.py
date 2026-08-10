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


if __name__ == "__main__":
    unittest.main()

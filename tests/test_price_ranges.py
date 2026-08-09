from __future__ import annotations

import sys
import unittest
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    CatalogConstraint,
    ConversationState,
    PriceConstraint,
    ProductRepository,
    ShoppingAgent,
    ShoppingRequirement,
)
from tests.test_product_repository import TestLLM


DATA_DIR = PROJECT_DIR / "data"


class CatalogPlanInsteadOfSelectionLLM:
    """Emulates the bad route from the reported screenshot."""

    def chat_json(self, _messages: list[dict]) -> dict:
        return {
            "goal": "information",
            "target": "catalog",
            "customer_reply": None,
            "requirement": {
                "item_type": None,
                "manufacturer": None,
                "price_constraint": None,
                "concepts": [],
                "needs_clarification": False,
                "clarification_question": None,
            },
            "catalog_operations": ["count"],
            "state_action": "none",
            "selection_mode": None,
            "action": None,
            "goal_evidence": [],
        }


class PriceRangeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ProductRepository(DATA_DIR)
        self.agent = ShoppingAgent(DATA_DIR)

    def test_parses_chinese_and_english_inclusive_ranges(self) -> None:
        cases = {
            "有没有10块以上20元以下的": (10.0, 20.0),
            "10 元到 20 元": (10.0, 20.0),
            "between $10 and $20": (10.0, 20.0),
        }
        for message, expected in cases.items():
            with self.subTest(message=message):
                constraint = self.agent._price_constraint_from_instruction(message)
                self.assertEqual((constraint.min_value, constraint.max_value), expected)
                self.assertTrue(constraint.min_inclusive)
                self.assertTrue(constraint.max_inclusive)

    def test_retrieval_respects_both_price_bounds(self) -> None:
        requirement = ShoppingRequirement(
            item_type=CatalogConstraint("马克杯", "hard", "mug"),
            price_constraint=PriceConstraint(min_value=10, max_value=20),
        )
        grounded = self.repository.ground(requirement)
        products, counts = self.repository.retrieve(grounded)

        self.assertTrue(products)
        self.assertTrue(all(product.item_type == "mug" for product in products))
        self.assertTrue(all(10 <= product.price <= 20 for product in products))
        self.assertLess(counts["after_price"], counts["after_item_type"])

    def test_pending_chinese_range_forces_selection_refinement(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        first = self.agent.run_turn("我想买一个马克杯", state)
        self.assertEqual(first["response_type"], "exploration")

        # The planner now makes the same read-only catalog mistake as the report.
        self.agent.llm = CatalogPlanInsteadOfSelectionLLM()
        result = self.agent.run_turn("有没有10块以上20元以下的", state)

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertTrue(10 <= selected.price <= 20)
        active = self.agent._reduce_requirement(state)
        self.assertEqual((active.price_constraint.min_value, active.price_constraint.max_value), (10, 20))
        transition = next(
            step for step in result["trace"] if step["step"] == "pending_price_refinement"
        )
        self.assertEqual(transition["status"], "enforced")

    def test_pending_english_range_forces_selection_refinement(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("I want a mug", state)
        self.agent.llm = CatalogPlanInsteadOfSelectionLLM()

        result = self.agent.run_turn("Between $10 and $20.", state)

        self.assertEqual(result["response_type"], "recommendation")
        selected = self.repository.by_id[result["purchased_product_id"]]
        self.assertTrue(10 <= selected.price <= 20)

    def test_reversed_range_is_not_applied(self) -> None:
        constraint = self.agent._price_constraint_from_instruction("20 元以上 10 元以下")
        self.assertFalse(constraint.has_value())

    def test_reversed_pending_range_asks_for_a_correction(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("我想买一个马克杯", state)

        result = self.agent.run_turn("20 元以上 10 元以下", state)

        self.assertEqual(result["response_type"], "clarification")
        self.assertIn("下限不能高于上限", result["summary"])
        self.assertEqual(
            result["conversation_state"]["pending_fields"], ["price_constraint"]
        )
        active = self.agent._reduce_requirement(state)
        self.assertFalse(active.price_constraint.has_value())

    def test_range_replaces_an_earlier_range_without_widening_it(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        first = self.agent.run_turn("I need a mug between $10 and $20.", state)
        second = self.agent.run_turn("From $12 to $18.", state)

        self.assertEqual(first["response_type"], "recommendation")
        self.assertEqual(second["response_type"], "recommendation")
        active = self.agent._reduce_requirement(state)
        self.assertEqual((active.price_constraint.min_value, active.price_constraint.max_value), (12, 18))
        selected = self.repository.by_id[second["purchased_product_id"]]
        self.assertTrue(12 <= selected.price <= 18)

    def test_single_chinese_lower_and_upper_bounds_remain_supported(self) -> None:
        lower = self.agent._price_constraint_from_instruction("10 元以上")
        upper = self.agent._price_constraint_from_instruction("20 元以下")

        self.assertEqual((lower.operator, lower.value), (">=", 10))
        self.assertEqual((upper.operator, upper.value), ("<=", 20))

    def test_out_of_catalog_range_reports_no_match(self) -> None:
        state = ConversationState()
        self.agent.llm = TestLLM(self.repository)
        self.agent.run_turn("我想买一个马克杯", state)

        result = self.agent.run_turn("21 元以上 22 元以下", state)

        self.assertEqual(result["response_type"], "no_match")
        grounded = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertEqual(
            (grounded["grounded_requirements"]["min_price"], grounded["grounded_requirements"]["max_price"]),
            (21, 22),
        )


if __name__ == "__main__":
    unittest.main()

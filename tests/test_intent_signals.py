from __future__ import annotations

import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    ConversationState,
    ProductRepository,
    ShoppingAgent,
    TurnPlan,
)

DATA_DIR = PROJECT_DIR / "data"


class IntentSignalTests(unittest.TestCase):
    """Test cases for improved intent recognition using signal preprocessing."""

    def setUp(self) -> None:
        self.repository = ProductRepository(DATA_DIR)
        self.agent = ShoppingAgent(DATA_DIR)

    def test_preprocess_detects_product_comparison_signal(self) -> None:
        """Product IDs + comparison words should be detected as comparison intent."""
        state = ConversationState()
        signals = self.agent._preprocess_intent_signals("比较 P0005 和 P0006", state)

        self.assertEqual(len(signals["explicit_product_ids"]), 2)
        self.assertTrue(signals["has_comparison_words"])
        self.assertTrue(signals["is_likely_comparison"])
        self.assertFalse(signals["is_likely_catalog_query"])
        self.assertFalse(signals["is_likely_transaction"])

    def test_preprocess_detects_catalog_query_signal(self) -> None:
        """Catalog query keywords without selection verbs should be detected."""
        state = ConversationState()
        signals = self.agent._preprocess_intent_signals("衬衫都有什么价位？", state)

        self.assertTrue(signals["has_catalog_query_words"])
        self.assertFalse(signals["has_selection_words"])
        self.assertTrue(signals["is_likely_catalog_query"])
        self.assertFalse(signals["is_likely_comparison"])

    def test_preprocess_detects_transaction_signal(self) -> None:
        """Product ID + transaction verb should be detected as transaction intent."""
        state = ConversationState()
        signals = self.agent._preprocess_intent_signals("下单 P0005", state)

        self.assertEqual(len(signals["explicit_product_ids"]), 1)
        self.assertTrue(signals["has_transaction_words"])
        self.assertTrue(signals["is_likely_transaction"])
        self.assertFalse(signals["is_likely_catalog_query"])

    def test_preprocess_distinguishes_selection_from_catalog_query(self) -> None:
        """Selection verb (想买) should override catalog query keywords."""
        state = ConversationState()

        # Catalog query: "有吗" without purchase intent
        catalog_signals = self.agent._preprocess_intent_signals("有Ocean主题的马克杯吗？", state)
        self.assertTrue(catalog_signals["has_catalog_query_words"])
        self.assertFalse(catalog_signals["has_selection_words"])
        self.assertTrue(catalog_signals["is_likely_catalog_query"])

        # Selection: "想买" with purchase intent
        selection_signals = self.agent._preprocess_intent_signals("我想买一个Ocean主题的马克杯", state)
        self.assertTrue(selection_signals["has_selection_words"])
        self.assertFalse(selection_signals["is_likely_catalog_query"])

    def test_preprocess_distinguishes_product_detail_from_transaction(self) -> None:
        """Product ID + price question vs product ID + purchase verb."""
        state = ConversationState()

        # Product detail: asking about price
        detail_signals = self.agent._preprocess_intent_signals("P0005多少钱？", state)
        self.assertEqual(len(detail_signals["explicit_product_ids"]), 1)
        self.assertTrue(detail_signals["has_catalog_query_words"])  # "多少钱"
        self.assertFalse(detail_signals["has_transaction_words"])

        # Transaction: purchasing
        transaction_signals = self.agent._preprocess_intent_signals("购买 P0005", state)
        self.assertEqual(len(transaction_signals["explicit_product_ids"]), 1)
        self.assertTrue(transaction_signals["has_transaction_words"])
        self.assertTrue(transaction_signals["is_likely_transaction"])

    def test_explicit_product_detail_signal_is_detected(self) -> None:
        state = ConversationState()
        signals = self.agent._preprocess_intent_signals("请介绍 P0005 的标签和价格", state)

        self.assertTrue(signals["has_product_detail_words"])
        self.assertTrue(signals["is_likely_product_detail"])
        self.assertFalse(signals["is_likely_transaction"])

    def test_strong_product_id_signals_reject_an_incompatible_plan(self) -> None:
        chat_plan = TurnPlan(goal="chat", target="none", customer_reply="好的")

        comparison_signals = self.agent._preprocess_intent_signals("比较 P0005 和 P0006", ConversationState())
        detail_signals = self.agent._preprocess_intent_signals("P0005 多少钱？", ConversationState())
        transaction_signals = self.agent._preprocess_intent_signals("下单 P0005", ConversationState())

        self.assertIsNotNone(self.agent._strong_signal_plan_mismatch(chat_plan, comparison_signals))
        self.assertIsNotNone(self.agent._strong_signal_plan_mismatch(chat_plan, detail_signals))
        self.assertIsNotNone(self.agent._strong_signal_plan_mismatch(chat_plan, transaction_signals))

    def test_goal_evidence_must_quote_the_latest_message_when_supplied(self) -> None:
        plan = TurnPlan(goal="selection", target="catalog", goal_evidence=["不存在的授权语句"])
        status, error = self.agent._goal_evidence_status(plan, "我想买一个马克杯", ConversationState())

        self.assertEqual(status, "mismatch")
        self.assertIsNotNone(error)

    def test_missing_evidence_is_audited_without_blocking_pending_followups(self) -> None:
        plan = TurnPlan(goal="selection", target="catalog", goal_evidence=[])
        status, error = self.agent._goal_evidence_status(
            plan,
            "Ocean 主题",
            ConversationState(pending_question="你更在意预算、主题，还是某个厂商？"),
        )

        self.assertEqual(status, "pending_follow_up")
        self.assertIsNone(error)

    def test_selection_evidence_paraphrase_is_audited_without_failing_the_turn(self) -> None:
        """A non-destructive selection must not fail solely on quoted wording."""
        class StaticPlanLLM:
            def chat_json(self, _messages: list[dict]) -> dict:
                return {
                    "goal": "selection",
                    "target": "catalog",
                    "customer_reply": None,
                    "requirement": {
                        "item_type": {
                            "raw_value": "马克杯",
                            "constraint_strength": "hard",
                            "catalog_hint": "mug",
                        },
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                    "catalog_operations": [],
                    "state_action": "merge",
                    "selection_mode": "criteria",
                    "action": None,
                    "goal_evidence": ["我想买马克杯"],
                }

        self.agent.llm = StaticPlanLLM()
        result = self.agent.run_turn("我想买个马克杯", ConversationState())

        self.assertEqual(result["response_type"], "exploration")
        evidence = next(step for step in result["trace"] if step["step"] == "goal_evidence")
        self.assertEqual(evidence["status"], "mismatch")

    def test_intent_signal_trace_is_recorded(self) -> None:
        """Intent signals should be logged in trace for debugging."""
        from tests.test_product_repository import TestLLM

        self.agent.llm = TestLLM(self.repository)
        state = ConversationState()
        result = self.agent.run_turn("比较 P0005 和 P0006", state)

        # Find intent signal detection step in trace
        signal_step = next(
            (step for step in result["trace"] if step.get("step") == "intent_signal_detection"),
            None
        )

        self.assertIsNotNone(signal_step)
        self.assertEqual(signal_step["status"], "completed")
        self.assertIn("signals", signal_step)
        self.assertTrue(signal_step["signals"]["is_likely_comparison"])

    def test_english_comparison_keywords_are_detected(self) -> None:
        """English comparison keywords should also work."""
        state = ConversationState()
        signals = self.agent._preprocess_intent_signals("compare P0005 vs. P0006", state)

        self.assertTrue(signals["has_comparison_words"])
        self.assertTrue(signals["is_likely_comparison"])

    def test_pending_question_continuation_signal(self) -> None:
        """Short message during pending question should be flagged."""
        state = ConversationState(
            pending_question="你的预算上限是多少？",
            pending_fields=["price_constraint"]
        )
        signals = self.agent._preprocess_intent_signals("30", state)

        self.assertTrue(signals["pending_question_continuation"])

    def test_multiple_selection_verbs_detected(self) -> None:
        """Various selection verbs should be recognized."""
        state = ConversationState()

        test_cases = [
            "我想买一件T恤",
            "需要一个马克杯",
            "推荐一件衬衫",
            "帮我选一个杯子",
            "I need a mug",
            "find me a shirt",
        ]

        for message in test_cases:
            with self.subTest(message=message):
                signals = self.agent._preprocess_intent_signals(message, state)
                self.assertTrue(
                    signals["has_selection_words"],
                    f"Failed to detect selection verb in: {message}"
                )


if __name__ == "__main__":
    unittest.main()

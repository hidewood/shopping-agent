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
)
from tests.test_product_repository import TestLLM

DATA_DIR = PROJECT_DIR / "data"


class CatalogHighlightTests(unittest.TestCase):
    """catalog_highlights must be deterministic and grounded in real rows."""

    def setUp(self) -> None:
        self.repository = ProductRepository(DATA_DIR)

    def test_empty_product_set_yields_no_counts(self) -> None:
        highlights = self.repository.catalog_highlights([])
        self.assertEqual(highlights["count"], 0)
        self.assertEqual(highlights["price_bands"], [])
        self.assertEqual(highlights["sample_products"], [])

    def test_price_bands_cover_every_product_exactly_once(self) -> None:
        mugs = [p for p in self.repository.products if p.item_type == "mug"]
        highlights = self.repository.catalog_highlights(mugs)
        self.assertEqual(sum(band["count"] for band in highlights["price_bands"]), len(mugs))
        self.assertEqual(highlights["price_min"], min(p.price for p in mugs))
        self.assertEqual(highlights["price_max"], max(p.price for p in mugs))

    def test_bands_are_ordered_and_non_overlapping(self) -> None:
        mugs = [p for p in self.repository.products if p.item_type == "mug"]
        bands = self.repository.catalog_highlights(mugs)["price_bands"]
        for earlier, later in zip(bands, bands[1:]):
            self.assertLessEqual(earlier["low"], earlier["high"])
            self.assertLessEqual(earlier["high"], later["low"])

    def test_highlights_are_deterministic(self) -> None:
        mugs = [p for p in self.repository.products if p.item_type == "mug"]
        first = self.repository.catalog_highlights(mugs)
        second = self.repository.catalog_highlights(list(reversed(mugs)))
        self.assertEqual(first, second)

    def test_tag_counts_match_the_supplied_products(self) -> None:
        mugs = [p for p in self.repository.products if p.item_type == "mug"]
        highlights = self.repository.catalog_highlights(mugs)
        for entry in highlights["top_tags"]:
            actual = sum(entry["value"] in product.tags for product in mugs)
            self.assertEqual(entry["count"], actual)

    def test_samples_are_the_cheapest_rows(self) -> None:
        mugs = [p for p in self.repository.products if p.item_type == "mug"]
        highlights = self.repository.catalog_highlights(mugs)
        expected = sorted(mugs, key=lambda p: (p.price, p.product_id))[:3]
        self.assertEqual(
            [p["product_id"] for p in highlights["sample_products"]],
            [p.product_id for p in expected],
        )


class ProactiveGuidanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.agent = ShoppingAgent(DATA_DIR)
        self.agent.llm = TestLLM(self.agent.repository)

    def test_clarification_offers_selectable_options(self) -> None:
        # No item type in message → clarification triggered; guidance uses text + example_phrases
        result = self.agent.run_turn("我想买个颜色好看的", ConversationState())
        guidance = result["proactive_guidance"]
        self.assertEqual(result["response_type"], "clarification")
        self.assertEqual(guidance["kind"], "selection_scope")
        self.assertTrue(guidance["example_phrases"])
        self.assertTrue(all(isinstance(p, str) for p in guidance["example_phrases"]))

    def test_exploration_scope_is_limited_to_the_named_item_type(self) -> None:
        # A type-only request introduces the matching catalog slice before refinement.
        result = self.agent.run_turn("我想买一件T恤", ConversationState())
        shirts = sum(p.item_type == "shirt" for p in self.agent.repository.products)
        self.assertEqual(result["response_type"], "exploration")
        self.assertEqual(result["proactive_guidance"]["kind"], "exploration_prompt")
        self.assertEqual(result["proactive_guidance"]["scope_product_count"], shirts)

    def test_catalog_query_guidance_describes_returned_rows(self) -> None:
        result = self.agent.run_turn("衬衫都有哪些价位？", ConversationState())
        guidance = result["proactive_guidance"]
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(guidance["kind"], "catalog_followup")
        self.assertEqual(guidance["scope_product_count"], result["catalog_data"]["total_count"])

    def test_no_match_guidance_reports_reachable_prices(self) -> None:
        state = ConversationState()
        self.agent.run_turn("I need a mug under $30.", state)
        result = self.agent.run_turn("Actually, under $8.", state)
        guidance = result["proactive_guidance"]
        self.assertEqual(result["response_type"], "no_match")
        self.assertEqual(guidance["kind"], "relaxation_hint")
        mugs = [p for p in self.agent.repository.products if p.item_type == "mug"]
        self.assertEqual(guidance["scope_product_count"], len(mugs))

    def test_zero_result_catalog_query_uses_relaxation_not_batch_wording(self) -> None:
        """A catalog query matching nothing must not describe "this batch"."""
        from tests.test_product_repository import StubLLM

        self.agent.llm = StubLLM([
            {
                "goal": "information", "target": "catalog", "customer_reply": None,
                "requirement": {
                    "item_type": {"raw_value": "衬衫", "constraint_strength": "hard", "catalog_hint": "shirt"},
                    "manufacturer": {"raw_value": "NoSuchMaker", "constraint_strength": "hard", "catalog_hint": None},
                    "price_constraint": None, "concepts": [],
                    "needs_clarification": False, "clarification_question": None,
                },
                "catalog_operations": ["count"], "state_action": "none",
                "selection_mode": None, "action": None, "goal_evidence": [],
            }
        ])
        result = self.agent.run_turn("NoSuchMaker 的衬衫有哪些？", ConversationState())
        guidance = result["proactive_guidance"]
        shirts = sum(p.item_type == "shirt" for p in self.agent.repository.products)
        self.assertEqual(result["catalog_data"]["total_count"], 0)
        self.assertEqual(guidance["kind"], "relaxation_hint")
        # Scope must stay on the type the user named, not widen to the whole catalog.
        self.assertEqual(guidance["scope_product_count"], shirts)

    def test_recommendation_with_large_pool_carries_narrowing_hint(self) -> None:
        # Many candidates → guidance attached so user knows what else is available.
        result = self.agent.run_turn(
            "A mug under $30; prefer Ocean themed products.", ConversationState()
        )
        self.assertEqual(result["response_type"], "recommendation")
        guidance = result.get("proactive_guidance")
        comparison = next(step for step in result["trace"] if step["step"] == "candidate_comparison")
        candidate_count = comparison["eligible_product_count"]
        if candidate_count > 5:
            self.assertIsNotNone(guidance)
            self.assertEqual(guidance["kind"], "narrowing_hint")
        else:
            self.assertIsNone(guidance)

    def test_chat_turn_carries_no_guidance(self) -> None:
        result = self.agent.run_turn("你好", ConversationState())
        self.assertEqual(result["response_type"], "chat")
        self.assertNotIn("proactive_guidance", result)

    def test_guidance_is_recorded_in_trace(self) -> None:
        # Use a no-type message so guidance is always attached (clarification path).
        result = self.agent.run_turn("我想买个颜色好看的", ConversationState())
        step = next(s for s in result["trace"] if s["step"] == "proactive_guidance")
        self.assertEqual(step["handler"], "deterministic_catalog_summary")
        self.assertEqual(
            step["example_phrase_count"], len(result["proactive_guidance"]["example_phrases"])
        )

    def test_guidance_does_not_mutate_shopping_state(self) -> None:
        state = ConversationState()
        self.agent.run_turn("我想买一件T恤", state)
        before = len([e for e in state.events if e.event_type == "constraint_update"])
        self.agent.run_turn("衬衫都有哪些价位？", state)
        after = len([e for e in state.events if e.event_type == "constraint_update"])
        self.assertEqual(before, after)

    def test_offered_replies_are_executable_turns(self) -> None:
        """Every example phrase must work when fed back as a user message."""
        # Use a no-type message so clarification guidance (with example_phrases) is attached.
        first = self.agent.run_turn("我想买个颜色好看的", ConversationState())
        for phrase in first["proactive_guidance"]["example_phrases"]:
            with self.subTest(phrase=phrase):
                follow_up = self.agent.run_turn(phrase, ConversationState())
                self.assertNotEqual(follow_up["response_type"], "service_error")

    def test_guidance_survives_a_state_round_trip(self) -> None:
        state = ConversationState()
        result = self.agent.run_turn("我想买一件T恤", state)
        stored = state.events[-1].payload["result"]
        self.assertEqual(stored["proactive_guidance"], result["proactive_guidance"])


if __name__ == "__main__":
    unittest.main()

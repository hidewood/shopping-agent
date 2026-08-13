"""Unit tests for ConversationState serialization round-trips (to_dict → from_dict)."""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    BundleLineItem,
    BundlePurchaseContext,
    Concept,
    ConversationEvent,
    ConversationState,
    PriceConstraint,
    TaskContext,
)


class ConversationEventTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        event = ConversationEvent(turn=3, event_type="user_message", payload={"message": "你好"})
        restored = ConversationEvent.from_dict(event.to_dict())
        self.assertEqual(restored.turn, 3)
        self.assertEqual(restored.event_type, "user_message")
        self.assertEqual(restored.payload["message"], "你好")


class TaskContextTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        ctx = TaskContext(
            active_task="selection",
            selection_phase="recommended",
            selected_product_id="P0005",
            candidate_product_ids=["P0005", "P0011"],
            last_information_target="catalog",
            last_information_operations=["count"],
            last_action="order.create",
        )
        restored = TaskContext.from_dict(ctx.to_dict())
        self.assertEqual(restored.active_task, "selection")
        self.assertEqual(restored.selection_phase, "recommended")
        self.assertEqual(restored.selected_product_id, "P0005")
        self.assertEqual(restored.candidate_product_ids, ["P0005", "P0011"])
        self.assertEqual(restored.last_action, "order.create")


class BundleContextTests(unittest.TestCase):
    def test_roundtrip(self) -> None:
        ctx = BundlePurchaseContext(
            item_types=["mug", "shirt"],
            line_items=[
                BundleLineItem(item_type="mug", quantity=2, concepts=[Concept("Ocean", "theme", "hard", ["Ocean"])]),
                BundleLineItem(item_type="shirt", quantity=1),
            ],
            selected_item_type="mug",
            budget_scope="combined",
            original_price_constraint=PriceConstraint(min_value=10, max_value=20),
        )
        restored = BundlePurchaseContext.from_dict(ctx.to_dict())
        self.assertEqual(restored.item_types, ["mug", "shirt"])
        self.assertEqual(restored.selected_item_type, "mug")
        self.assertEqual(restored.budget_scope, "combined")
        self.assertEqual(restored.original_price_constraint.min_value, 10)
        self.assertEqual(restored.line_items[0].quantity, 2)
        self.assertEqual(restored.line_items[0].concepts[0].raw_value, "Ocean")


class ConversationStateTests(unittest.TestCase):
    def test_empty_state_roundtrip(self) -> None:
        state = ConversationState(conversation_id="conv-abc123")
        restored = ConversationState.from_dict(state.to_dict())
        self.assertEqual(restored.conversation_id, "conv-abc123")
        self.assertEqual(restored.events, [])
        self.assertEqual(restored.turn_count, 0)

    def test_full_state_roundtrip(self) -> None:
        state = ConversationState(conversation_id="conv-full")
        state.add_event("user_message", {"message": "我想买个马克杯"})
        state.turn_count = 2
        state.status = "recommendation"
        state.pending_question = "预算多少？"
        state.pending_fields = ["price_constraint"]
        state.task_context = TaskContext(active_task="selection", selection_phase="recommended")
        state.saved_product_ids = ["P0005"]
        state.simulated_orders = [{"order_id": "SIM-0001", "status": "confirmed_local"}]
        state.preference_profile.record_product(  # 需要一个 product，这里用 minimal 假对象
            type("P", (), {"manufacturer": "X", "item_type": "mug", "tags": ["Ocean"]})(), signal="favorite"
        )
        state.bundle_context = BundlePurchaseContext(item_types=["mug", "shirt"])

        restored = ConversationState.from_dict(state.to_dict())

        self.assertEqual(restored.conversation_id, "conv-full")
        self.assertEqual(restored.turn_count, 2)
        self.assertEqual(restored.status, "recommendation")
        self.assertEqual(restored.pending_question, "预算多少？")
        self.assertEqual(restored.pending_fields, ["price_constraint"])
        self.assertEqual(restored.task_context.active_task, "selection")
        self.assertEqual(restored.saved_product_ids, ["P0005"])
        self.assertEqual(restored.simulated_orders[0]["order_id"], "SIM-0001")
        self.assertTrue(restored.preference_profile.has_signal())
        self.assertEqual(restored.bundle_context.item_types, ["mug", "shirt"])
        self.assertEqual(len(restored.events), 1)

    def test_roundtrip_is_stable(self) -> None:
        """二次序列化应得到相同结果（幂等）。"""
        state = ConversationState(conversation_id="conv-stable")
        state.add_event("user_message", {"message": "hi"})
        once = state.to_dict()
        twice = ConversationState.from_dict(once).to_dict()
        self.assertEqual(once, twice)


if __name__ == "__main__":
    unittest.main()

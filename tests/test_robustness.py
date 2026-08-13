"""Unit tests for the robustness hardening added after real-API testing.

These cover the tolerance logic that mock LLMs could never exercise: model
output drift such as field-name typos, tool-call-style catalog_operations,
Chinese quote marks, and truncated JSON.
"""

from __future__ import annotations

import unittest
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import ShoppingAgent, TurnPlan, _normalize_plan_data
from starter.llm_client import DeepSeekClient, LLMResponseError


class SimulatedOrderIdTests(unittest.TestCase):
    def test_parses_order_id_without_space_after_chinese(self) -> None:
        """\b fails between Chinese and ASCII; the ASCII-boundary regex must work."""
        self.assertEqual(ShoppingAgent._simulated_order_id("取消模拟订单SIM-0001"), "SIM-0001")
        self.assertEqual(ShoppingAgent._simulated_order_id("取消模拟订单 SIM-0001"), "SIM-0001")
        self.assertEqual(ShoppingAgent._simulated_order_id("请取消订单SIM-0001"), "SIM-0001")

    def test_rejects_order_id_embedded_in_longer_token(self) -> None:
        self.assertIsNone(ShoppingAgent._simulated_order_id("XSIM-0001X"))


class NormalizePlanDataTests(unittest.TestCase):
    def test_accepts_requirements_plural(self) -> None:
        data = {
            "goal": "selection", "target": "catalog",
            "requirements": {"item_type": {"raw_value": "mug", "constraint_strength": "hard", "catalog_hint": "mug"}},
            "catalog_operations": [], "state_action": "merge", "selection_mode": "criteria",
            "action": None, "goal_evidence": [],
        }
        plan = TurnPlan.from_dict(data)
        self.assertEqual(plan.goal, "selection")
        self.assertEqual(plan.requirement.item_type.catalog_hint, "mug")

    def test_corrects_selection_target_from_product_to_catalog(self) -> None:
        data = {
            "goal": "selection", "target": "product",
            "requirement": {"item_type": {"raw_value": "T恤", "constraint_strength": "hard", "catalog_hint": "shirt"}},
            "catalog_operations": [], "state_action": "merge", "selection_mode": "criteria",
            "action": None, "goal_evidence": [],
        }
        plan = TurnPlan.from_dict(data)
        self.assertEqual(plan.target, "catalog")

    def test_extracts_catalog_operations_from_dicts(self) -> None:
        data = {
            "goal": "information", "target": "catalog",
            "requirement": {"item_type": None},
            "catalog_operations": [{"operation": "count"}, {"operation": "group_by_tag"}],
            "state_action": "none", "selection_mode": None, "action": None, "goal_evidence": [],
        }
        plan = TurnPlan.from_dict(data)
        self.assertEqual(plan.catalog_operations, ["count", "group_by_tag"])

    def test_drops_unknown_tool_call_operations(self) -> None:
        """A tool-call style 'search' operation is dropped rather than aborting."""
        data = {
            "goal": "selection", "target": "catalog",
            "requirement": {"item_type": {"raw_value": "T恤", "constraint_strength": "hard", "catalog_hint": "shirt"}},
            "catalog_operations": [{"operation": "search", "criteria": {}}],
            "state_action": "merge", "selection_mode": "criteria",
            "action": None, "goal_evidence": [],
        }
        plan = TurnPlan.from_dict(data)
        self.assertEqual(plan.catalog_operations, [])


class ParseJsonTests(unittest.TestCase):
    def test_handles_chinese_quotes(self) -> None:
        content = '{"goal": "chat", "customer_reply": "你好"}'.replace('"', '“').replace('"', '”')
        # Build a string with Chinese curly quotes around keys/values
        content = '{"goal": "chat", "customer_reply": "你好"}'
        content = content.replace('"goal"', '“goal”').replace('"chat"', '“chat”')
        data = DeepSeekClient._parse_json(content)
        self.assertEqual(data.get("goal"), "chat")

    def test_recovers_truncated_json_by_adding_braces(self) -> None:
        content = '{"goal": "chat", "customer_reply": "你好"'
        data = DeepSeekClient._parse_json(content)
        self.assertEqual(data.get("goal"), "chat")

    def test_strips_markdown_fence(self) -> None:
        content = '```json\n{"goal": "chat"}\n```'
        data = DeepSeekClient._parse_json(content)
        self.assertEqual(data.get("goal"), "chat")

    def test_extracts_json_from_surrounding_text(self) -> None:
        content = '好的，以下是计划：{"goal": "chat", "customer_reply": "你好"} 请查收。'
        data = DeepSeekClient._parse_json(content)
        self.assertEqual(data.get("goal"), "chat")

    def test_raises_on_totally_invalid_json(self) -> None:
        with self.assertRaises(LLMResponseError):
            DeepSeekClient._parse_json("not json at all")


if __name__ == "__main__":
    unittest.main()

"""Opt-in DeepSeek smoke tests; excluded from normal offline regression runs."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path


PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import Agent, ConversationState


@unittest.skipUnless(
    os.getenv("RUN_LIVE_API_TESTS") == "1",
    "Set RUN_LIVE_API_TESTS=1 and configure DEEPSEEK_API_KEY to run live API smoke tests.",
)
class LiveAPISmokeTests(unittest.TestCase):
    def test_chat_and_compound_catalog_plan(self) -> None:
        agent = Agent(PROJECT_DIR / "data")
        state = ConversationState()
        chat = agent.run_turn("你好", state)
        catalog = agent.run_turn("你家有衬衫出售吗？都有哪些风格的衬衫呢？", state)

        self.assertNotEqual(chat["response_type"], "service_error", chat["trace"])
        self.assertNotEqual(catalog["response_type"], "service_error", catalog["trace"])
        self.assertEqual(catalog["response_type"], "catalog_query")
        self.assertIn("tag", catalog["catalog_data"].get("facets", {}))


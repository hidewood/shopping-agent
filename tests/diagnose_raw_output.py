"""Capture raw model output for the 3 flaky cases to see remaining failure modes."""

from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    Agent, ConversationState, TurnPlan, turn_planner_messages,
)


CASES = [
    ("[44] 不要杯子了，换成T恤", ["我想买个马克杯，预算20以内"], "不要杯子了，换成T恤"),
    ("[46] 我想买个海洋主题的马克杯", [], "我想买个海洋主题的马克杯"),
    ("[49] 给我找个Ocean themed mug", [], "给我找个Ocean themed mug"),
]


def main() -> None:
    agent = Agent(PROJECT_DIR / "data")
    for label, ctx, msg in CASES:
        print(f"\n{'=' * 70}")
        print(label)
        for i in range(3):
            state = ConversationState()
            for prior in ctx:
                agent.run_turn(prior, state)
            # 构造 planner messages 并直接调用 LLM 拿原始输出
            prev = agent._reduce_requirement(state)
            signals = agent._preprocess_intent_signals(msg, state)
            messages = turn_planner_messages(
                msg, agent.repository.catalog(),
                {"recent_messages": agent._recent_conversation_messages(state)},
                agent._shopping_context(state, prev),
                state.last_catalog_context,
                signals,
                agent.language_config,
            )
            try:
                raw = agent.llm.chat_json(messages)
                plan = TurnPlan.from_dict(raw)
                print(f"  第{i+1}次: OK goal={plan.goal} target={plan.target}")
            except Exception as exc:
                err = getattr(exc, "error_code", "?")
                print(f"  第{i+1}次: FAIL({err}) {exc}")
                # 尝试再次拿原始文本
                try:
                    resp = agent.llm._client.chat.completions.create(
                        model=agent.llm._model, messages=messages, temperature=0.2,
                        response_format={"type": "json_object"},
                    )
                    content = resp.choices[0].message.content
                    print(f"     原始输出: {content[:300]}")
                except Exception as e2:
                    print(f"     无法获取原始输出: {e2}")


if __name__ == "__main__":
    main()

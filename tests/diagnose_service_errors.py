"""Re-run the 10 service_error cases with full trace error detail.

For each case, print the model_call / turn_planning / turn_plan_repair steps so
we can see exactly what the real model returned and why validation rejected it.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import Agent, ConversationState


# Each entry: (label, multi_turn_context_before, message)
# multi_turn_context_before is a list of prior messages to replay to rebuild state.
CASES = [
    ("[9] 海洋主题的 (多轮延续)", ["我想买个马克杯", "预算15以内"], "海洋主题的"),
    ("[17] 有没有Ocean主题的马克杯啊", [], "有没有Ocean主题的马克杯啊"),
    ("[41] 我想买个马克杯，预算30以内", [], "我想买个马克杯，预算30以内"),
    ("[42] 算了，不用管预算了", ["我想买个马克杯，预算30以内"], "算了，不用管预算了"),
    ("[44] 不要杯子了，换成T恤", ["我想买个马克杯，预算20以内"], "不要杯子了，换成T恤"),
    ("[46] 我想买个海洋主题的马克杯", [], "我想买个海洋主题的马克杯"),
    ("[49] 给我找个Ocean themed mug", [], "给我找个Ocean themed mug"),
    ("[50] find me a shirt, budget 20", [], "find me a shirt, budget 20"),
    ("[59] 再给我看看T恤，不限预算", ["你好，我想给朋友买个礼物", "马克杯吧", "预算20以内，要海洋主题的", "这个不错，收藏它"], "再给我看看T恤，不限预算"),
    ("[60] 推荐一件T恤", ["你好，我想给朋友买个礼物", "马克杯吧", "预算20以内，要海洋主题的", "这个不错，收藏它", "再给我看看T恤，不限预算"], "推荐一件T恤"),
]


def dump_error_steps(trace: list[dict]) -> None:
    for step in trace:
        if step.get("status") in ("failed", "skipped", "requested"):
            print(f"      · trace[{step.get('step')}] status={step.get('status')} "
                  f"error_code={step.get('error_code', '-')} warning={str(step.get('warning', ''))[:200]}")
        elif step.get("step") == "turn_plan_repair" and step.get("status") == "completed":
            print(f"      · trace[turn_plan_repair] status=completed")


def main() -> None:
    agent = Agent(PROJECT_DIR / "data")
    for label, context, message in CASES:
        state = ConversationState()
        print(f"\n{'=' * 70}")
        print(f"{label}")
        print(f"  输入: {message}")
        # Rebuild prior context
        for prior in context:
            agent.run_turn(prior, state)
        result = agent.run_turn(message, state)
        print(f"  类型: {result.get('response_type')}")
        print(f"  回复: {result.get('summary', '')[:150]}")
        if result.get("response_type") == "service_error":
            dump_error_steps(result.get("trace", []))


if __name__ == "__main__":
    main()

"""Run the manual test cases against the real DeepSeek API.

Usage:
    python tests/run_manual_tests.py            # run all cases
    python tests/run_manual_tests.py 1-16       # run cases #1..#16

Each multi-turn group shares one ConversationState; single-turn cases each use
a fresh state.  Output is printed as (input → response_type → summary) so you
can eyeball whether the agent behaved as documented.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import Agent, ConversationState


# (group_name, multi_turn, [(message, expected), ...])
# multi_turn=True → the cases share one ConversationState in order.
TEST_GROUPS = [
    ("基础对话", False, [
        ("你好", "打招呼"),
        ("你能帮我做什么？", "说明能力边界"),
        ("今天天气怎么样", "拒绝非购物话题"),
    ]),
    ("商品探索", False, [
        ("我想买个马克杯", "展示概览并引导"),
        ("有没有T恤", "展示T恤概览"),
        ("帮我看看杯子", "展示马克杯概览"),
    ]),
    ("多轮筛选", True, [
        ("我想买个马克杯", "进入探索"),
        ("预算15以内", "按预算过滤"),
        ("海洋主题的", "追加主题过滤"),
        ("换个便宜点的，10块以内", "更新预算"),
    ]),
    ("直接推荐", False, [
        ("推荐一件T恤，不限预算和风格", "直接推荐T恤"),
        ("随便给我挑个马克杯", "开放式推荐"),
        ("帮我选一件衬衫，什么都行", "直接推荐衬衫"),
    ]),
    ("目录查询", False, [
        ("你们家的衬衫都什么价位？", "价格区间"),
        ("最便宜的马克杯多少钱？", "最便宜价格"),
        ("最贵的T恤是哪个？", "最贵商品"),
        ("有没有Ocean主题的马克杯啊", "数量统计"),
        ("衬衫都有哪些风格的？", "风格统计"),
    ]),
    ("商品详情比较", False, [
        ("P0005是什么商品", "详情"),
        ("P0005多少钱", "价格"),
        ("比较一下P0005和P0011", "并列比较"),
        ("给我介绍下P0011的标签", "标签描述"),
    ]),
    ("无结果处理", False, [
        ("我想要Ocean主题的马克杯，预算5块以内", "无匹配+最近结果"),
        ("有没有Disney主题的马克杯", "目录无此主题"),
        ("我想买个相机", "目录无此类型"),
    ]),
    ("组合方案", False, [
        ("我想买马克杯和T恤，每件预算20以内，给我组合方案", "分别推荐"),
        ("马克杯和T恤，总预算25以内，给我一套", "合计预算组合"),
        ("我想买2个马克杯和1件T恤，总预算30以内", "数量×价格组合"),
        ("我想买一个马克杯和一个T恤，预算20", "询问预算归属"),
    ]),
    ("收藏与模拟订单", True, [
        ("收藏P0005", "收藏成功"),
        ("看看我的收藏", "列出收藏"),
        ("创建模拟订单P0005", "创建订单"),
        ("查看模拟订单", "列出订单"),
        ("取消模拟订单SIM-0001", "取消订单"),
    ]),
    ("交易边界", False, [
        ("帮我下单P0005", "拒绝下单"),
        ("支付P0005", "拒绝支付"),
        ("我要买P0005", "拒绝真实交易"),
    ]),
    ("价格相关", False, [
        ("有没有10块以上20块以下的马克杯", "价格区间"),
        ("20块以上10块以下", "提示价格反转"),
        ("我想买个马克杯，预算30以内", "推荐"),
    ]),
    ("价格清除", True, [
        ("我想买个马克杯，预算30以内", "推荐"),
        ("算了，不用管预算了", "清除预算重新推荐"),
    ]),
    ("类型切换", True, [
        ("我想买个马克杯，预算20以内", "推荐马克杯"),
        ("不要杯子了，换成T恤", "切换T恤"),
    ]),
    ("偏好vs硬约束", False, [
        ("我想买个马克杯，优先海洋主题", "海洋为偏好"),
        ("我想买个海洋主题的马克杯", "海洋为硬约束"),
        ("我喜欢复古风的马克杯", "复古为偏好"),
    ]),
    ("中英文混合", False, [
        ("I want a mug under 30 dollars", "英文推荐"),
        ("给我找个Ocean themed mug", "中英混合"),
        ("find me a shirt, budget 20", "英文推荐T恤"),
    ]),
    ("边界异常", False, [
        ("", "空消息提示"),
        ("我想要11个马克杯，总预算300", "超量提示"),
        ("帮朋友挑个礼物", "询问类型"),
        ("有没有15块以下的马克杯，Ocean主题的", "组合过滤"),
    ]),
    ("完整购物流程", True, [
        ("你好，我想给朋友买个礼物", "询问类型"),
        ("马克杯吧", "展示概览"),
        ("预算20以内，要海洋主题的", "推荐海洋马克杯"),
        ("这个不错，收藏它", "收藏"),
        ("再给我看看T恤，不限预算", "切换T恤"),
        ("推荐一件T恤", "直接推荐"),
        ("创建模拟订单", "创建订单"),
        ("查看我的收藏和订单", "列出收藏订单"),
    ]),
]


def main() -> None:
    agent = Agent(PROJECT_DIR / "data")

    # Optional range filter: python run_manual_tests.py 1-16
    start = end = None
    if len(sys.argv) > 1 and "-" in sys.argv[1]:
        start, end = (int(x) for x in sys.argv[1].split("-"))

    case_no = 0
    for group_name, multi_turn, cases in TEST_GROUPS:
        state = ConversationState()
        print(f"\n{'=' * 60}")
        print(f"【{group_name}】")
        print("=" * 60)
        for message, expected in cases:
            case_no += 1
            if start and (case_no < start or case_no > end):
                continue
            print(f"\n[{case_no}] 输入: {message or '(空)'}")
            print(f"     预期: {expected}")
            result = agent.run_turn(message, state)
            print(f"     类型: {result.get('response_type', '?')}")
            summary = result.get("summary", "")
            print(f"     回复: {summary[:200]}")
            if result.get("purchased_product_id"):
                print(f"     推荐: {result['purchased_product_id']}")
            if not multi_turn:
                state = ConversationState()  # 单轮每组独立


if __name__ == "__main__":
    main()

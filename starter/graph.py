"""LangGraph 实现的 ReAct 循环（检索-观察-调整）。

用 LangGraph 把 tool-calling 循环表达成显式的图：LLM 规划 → 工具执行 →
观察结果 → 决定继续或结束。相比手写的 ``run_turn_with_tools``，图结构让
循环、路由和未来扩展（多步推理、条件分支）更清晰。

与现有的 ``run_turn()`` 并存，不改变现有行为。
"""

from __future__ import annotations

import json
from typing import Any

from langgraph.graph import END, START, StateGraph

MAX_ROUNDS = 5


def create_react_graph(agent: Any):
    """构建 ReAct graph。agent 是 ShoppingAgent 实例（需有 llm/_execute_tool/TOOL_DEFINITIONS）。"""

    def llm_node(state: dict) -> dict:
        """调用 LLM（带工具），返回工具调用或最终回答。"""
        messages = state["messages"]
        response = agent.llm._client.chat.completions.create(
            model=agent.llm._model,
            messages=messages,
            tools=agent.TOOL_DEFINITIONS,
            temperature=0.2,
        )
        msg = response.choices[0].message
        rounds = state.get("rounds", 0) + 1

        new_messages = messages + [{"role": "assistant", "content": msg.content}]
        tool_calls = []
        if msg.tool_calls:
            tool_calls = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.function.name, "arguments": tc.function.arguments}}
                for tc in msg.tool_calls
            ]
            new_messages[-1]["tool_calls"] = tool_calls

        return {
            "messages": new_messages,
            "rounds": rounds,
            "tool_calls": tool_calls,
            "final_answer": msg.content if not msg.tool_calls else None,
        }

    def tools_node(state: dict) -> dict:
        """执行最后一条 assistant 消息里的工具调用。"""
        messages = state["messages"]
        for tc in state["tool_calls"]:
            args = json.loads(tc["function"]["arguments"]) if tc["function"]["arguments"] else {}
            result = agent._execute_tool(tc["function"]["name"], args)
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        return {"messages": messages, "tool_calls": []}

    def should_continue(state: dict) -> str:
        if state["tool_calls"] and state.get("rounds", 0) < MAX_ROUNDS:
            return "tools"
        return "end"

    def finalize_node(state: dict) -> dict:
        """没有更多工具调用时，返回最终回答。"""
        return {"final_answer": state["final_answer"] or "抱歉，我暂时无法完成这个查询。"}

    builder = StateGraph(dict)
    builder.add_node("llm", llm_node)
    builder.add_node("tools", tools_node)
    builder.add_node("finalize", finalize_node)
    builder.add_edge(START, "llm")
    builder.add_conditional_edges("llm", should_continue, {"tools": "tools", "end": "finalize"})
    builder.add_edge("tools", "llm")
    builder.add_edge("finalize", END)
    return builder.compile()


def run_react(agent: Any, message: str, state: Any) -> dict:
    """用 LangGraph ReAct graph 运行一轮，返回 {response_type, summary, trace}。"""
    from starter.agent_interface import LLMResponseError

    graph = create_react_graph(agent)
    trace: list[dict[str, Any]] = [{"step": "react_graph", "status": "received", "mode": "langgraph"}]

    try:
        result = graph.invoke({
            "messages": [{"role": "system", "content": agent.TOOL_SYSTEM_PROMPT},
                         {"role": "user", "content": message}],
            "rounds": 0,
            "tool_calls": [],
            "final_answer": None,
        })
        answer = result.get("final_answer", "")
        trace.append({"step": "react_graph", "status": "completed", "rounds": result.get("rounds", 0)})
        return {
            "response_type": "chat",
            "summary": answer,
            "trace": trace,
            "purchased_product_id": None,
        }
    except LLMResponseError as exc:
        trace.append({"step": "model_service", "status": "failed", "error_code": exc.error_code})
        return {
            "response_type": "service_error",
            "summary": "模型服务暂不可用，请稍后重试。",
            "trace": trace,
            "purchased_product_id": None,
        }

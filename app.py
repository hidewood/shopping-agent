from __future__ import annotations

from html import escape
from pathlib import Path
from typing import Any

import streamlit as st

from starter.agent_interface import FEW_RESULTS_THRESHOLD, Agent, ConversationState


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
PAGE_SIZE = 12
AGENT_IMPLEMENTATION_VERSION = str((PROJECT_DIR / "starter" / "agent_interface.py").stat().st_mtime_ns)

# Design tokens — single source of truth for all colours and spacing.
THEME = {
    "bg": "#f5f8fc",
    "text": "#17385f",
    "primary": "#1d61aa",
    "primary_light": "#2479d0",
    "accent_bg": "#eaf4ff",
    "card_bg": "#ffffff",
    "card_border": "#bed6ee",
    "muted": "#6d8098",
    "radius": "13px",
    "shadow": "0 3px 13px rgba(25,86,157,.07)",
}


def apply_style() -> None:
    # Use Streamlit's config API for stable settings; keep only custom CSS here.
    st.markdown(
        f"""
        <style>
          .stApp {{ background: {THEME["bg"]}; color: {THEME["text"]}; }}
          .block-container {{ max-width: 1220px; padding-top: 1.35rem; padding-bottom: 3rem; }}
          .hero {{ background: linear-gradient(110deg, #edf6ff, #ffffff 64%, #e0f0ff); border: 1px solid #c8def4; border-radius: 16px; padding: 22px 30px; margin-bottom: 1.1rem; }}
          .hero h1 {{ color: #17549b; margin: 0; font-size: 2rem; }}
          .hero p {{ color: #577693; margin: .4rem 0 0; }}
          .section-title {{ border-left: 5px solid {THEME["primary_light"]}; color: #1b5695; padding-left: 10px; font-size: 1.1rem; font-weight: 750; margin: .25rem 0 .85rem; }}
          .result-card, .alternative-card {{ background: {THEME["card_bg"]}; border: 1px solid {THEME["card_border"]}; border-radius: {THEME["radius"]}; padding: 16px 18px; box-shadow: {THEME["shadow"]}; }}
          .alternative-card {{ min-height: 188px; box-shadow: none; }}
          .price {{ color: #1264b8; font-size: 1.55rem; font-weight: 750; margin: .05rem 0 .55rem; }}
          .tag {{ display: inline-block; color: #2264a6; background: {THEME["accent_bg"]}; border: 1px solid #cce2f7; border-radius: 99px; padding: .16rem .5rem; margin: 0 .28rem .28rem 0; font-size: .82rem; }}
          .muted {{ color: {THEME["muted"]}; font-size: .9rem; }}
          .summary-box {{ background: {THEME["accent_bg"]}; border-left: 4px solid #2376c9; border-radius: 6px; padding: .8rem 1rem; color: #174b83; margin-top: .85rem; }}
          .guidance-box {{ background: #f8fbff; border: 1px solid #d7e7f7; border-radius: 9px; padding: .65rem .85rem; color: #315d87; margin-top: .7rem; font-size: .92rem; }}
          .guidance-examples {{ color: #55728e; margin-top: .35rem; line-height: 1.65; }}
          .guidance-example {{ display: inline-block; background: #fff; border: 1px solid #cce2f7; border-radius: 99px; padding: .06rem .46rem; margin: .1rem .25rem .1rem 0; color: #2464a5; }}
          .stChatInput textarea {{ border: 1px solid #b8cde6; border-radius: 9px; }}
          .stButton > button {{ background: {THEME["primary"]}; color: #fff; border: 1px solid {THEME["primary"]}; border-radius: 7px; font-weight: 600; }}
          .stButton > button:hover {{ background: #174f8a; border-color: #174f8a; color: #fff; }}
          .stExpander {{ background: #fff; border: 1px solid #d4e2f0; border-radius: 9px; }}
          @media (max-width: 720px) {{
            .block-container {{ padding: .9rem .75rem 2rem; }}
            .hero {{ padding: 18px 20px; }}
            .hero h1 {{ font-size: 1.6rem; }}
            .result-card, .alternative-card {{ padding: 13px 14px; }}
            .price {{ font-size: 1.35rem; }}
          }}
        </style>
        """,
        unsafe_allow_html=True,
    )


@st.cache_resource(show_spinner=False)
def get_agent(implementation_version: str) -> Agent:
    """Invalidate the cached Agent whenever its implementation file changes."""
    _ = implementation_version
    return Agent(DATA_DIR)


def trace_item(trace: list[dict[str, Any]], step: str) -> dict[str, Any]:
    return next((item for item in trace if item.get("step") == step), {})


def render_tags(tags: list[str]) -> str:
    return "".join(f'<span class="tag">{tag}</span>' for tag in tags) or '<span class="muted">无标签</span>'


def show_closest_alternatives(agent: Agent, result: dict[str, Any]) -> None:
    """Show the near misses behind a no-match answer, one card per relaxation."""
    alternatives = result.get("catalog_data", {}).get("alternatives", [])
    if not alternatives:
        return
    labels = {"price": "放宽预算后", "manufacturer": "不限厂商后", "tags": "不限主题后"}
    columns = st.columns(min(3, len(alternatives)))
    for column, alternative in zip(columns, alternatives):
        with column:
            product = alternative["products"][0]
            product_card(
                agent.repository.by_id[product["product_id"]],
                title=labels.get(alternative["relaxed_constraint"], "最接近"),
            )


def product_card(product: Any, *, title: str, primary: bool = False) -> None:
    card_class = "result-card" if primary else "alternative-card"
    st.markdown(
        f"""
        <div class="{card_class}">
          <div class="muted">{title} · {product.product_id} · {product.item_type}</div>
          <h3 style="margin:.3rem 0;color:#1b4f89">{product.name}</h3>
          <div class="price">${product.price:.2f}</div>
          <div>{render_tags(product.tags)}</div>
          <p class="muted" style="margin:.55rem 0 0">厂商：{product.manufacturer}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )


def readable_requirements(grounded: dict[str, Any]) -> list[str]:
    if not grounded:
        return []
    items = []
    if grounded.get("item_type"):
        items.append(f"商品类型：{grounded['item_type']}")
    if grounded.get("hard_manufacturer"):
        items.append(f"必须厂商：{grounded['hard_manufacturer']}")
    elif grounded.get("preferred_manufacturer"):
        items.append(f"优先厂商：{grounded['preferred_manufacturer']}")
    if grounded.get("min_price") is not None and grounded.get("max_price") is not None:
        lower = "≤" if grounded.get("min_price_inclusive", True) else "<"
        upper = "≤" if grounded.get("max_price_inclusive", True) else "<"
        items.append(
            "预算："
            # ``st.caption`` parses Markdown.  A pair of dollar signs would be
            # interpreted as inline LaTeX and render the range in a mismatched
            # math font, so use an explicit currency label here.
            f"USD {grounded['min_price']:.2f} {lower} 价格 {upper} USD {grounded['max_price']:.2f}"
        )
    elif grounded.get("min_price") is not None:
        operator = "≥" if grounded.get("min_price_inclusive", True) else ">"
        items.append(f"预算：价格 {operator} USD {grounded['min_price']:.2f}")
    elif grounded.get("max_price") is not None:
        operator = "≤" if grounded.get("max_price_inclusive", True) else "<"
        items.append(f"预算：价格 {operator} USD {grounded['max_price']:.2f}")
    elif grounded.get("price_value") is not None:
        items.append(
            f"预算：价格 {grounded.get('price_operator')} USD {grounded['price_value']:.2f}"
        )
    required_groups = grounded.get("required_tag_groups") or [
        [tag] for tag in grounded.get("required_tags", [])
    ]
    preferred_groups = grounded.get("preferred_tag_groups") or [
        [tag] for tag in grounded.get("preferred_tags", [])
    ]
    if required_groups:
        items.append("硬性主题：" + "；".join(" 或 ".join(group) for group in required_groups))
    if preferred_groups:
        items.append("偏好主题：" + "；".join(" 或 ".join(group) for group in preferred_groups))
    return items


def friendly_summary(agent: Agent, result: dict[str, Any]) -> str:
    trace = result["trace"]
    handlers = {item.get("handler") for item in trace if item.get("handler")}
    if result["purchased_product_id"] is None:
        return result["summary"]
    product = agent.repository.by_id[result["purchased_product_id"]]
    text = f"系统推荐 {product.name}，价格 ${product.price:.2f}。"
    if "deterministic_ranking" in handlers or "rule_based" in handlers:
        text += "模型已完成需求理解与计划；商品工具随后按已验证偏好、价格和商品 ID 的规则完成排序。"
    else:
        text += "该结果通过了商品目录与硬约束校验。"
    return text


def show_decision_evidence(
    agent: Agent, result: dict[str, Any], *, include_trace_expander: bool = True
) -> None:
    trace = result["trace"]
    grounding = trace_item(trace, "catalog_grounding").get("grounded_requirements", {})
    retrieval = trace_item(trace, "retrieval_and_hard_filtering")
    comparison = trace_item(trace, "candidate_comparison")

    requirements = readable_requirements(grounding)
    if requirements:
        st.write("已识别的条件：" + "；".join(requirements))
    elif result["purchased_product_id"] is None:
        st.write("当前需求信息不足或存在无法满足的硬条件。")

    counts = retrieval.get("filter_counts", {})
    if counts:
        stages = [
            ("商品库", counts.get("total_products", 0)),
            ("类别过滤后", counts.get("after_item_type", 0)),
            ("厂商过滤后", counts.get("after_hard_manufacturer", 0)),
            ("预算过滤后", counts.get("after_price", 0)),
            ("标签过滤后", counts.get("after_required_tags", 0)),
        ]
        columns = st.columns(len(stages))
        for column, (label, value) in zip(columns, stages):
            column.metric(label, value)

    candidate_ids = comparison.get("candidate_product_ids", [])
    if candidate_ids:
        st.caption(f"系统从符合硬条件的商品中预选 {len(candidate_ids)} 件进入候选比较。")

    safe_trace = []
    for item in trace:
        copied = dict(item)
        copied.pop("eligible_product_ids", None)
        safe_trace.append(copied)
    if include_trace_expander:
        with st.expander("查看结构化核验记录"):
            st.json(safe_trace, expanded=False)
    else:
        st.json(safe_trace, expanded=False)


def show_recommendation(agent: Agent, result: dict[str, Any]) -> None:
    product_id = result["purchased_product_id"]
    if product_id is None:
        st.warning(result["summary"])
        show_decision_evidence(agent, result)
        return

    selected = agent.repository.by_id[product_id]
    trace = result["trace"]
    grounding = trace_item(trace, "catalog_grounding").get("grounded_requirements", {})
    requirements = readable_requirements(grounding)

    # Keep the conversation first: the user sees a grounded recommendation and
    # its applied conditions before inspecting the visual product evidence.
    st.markdown(f'<div class="summary-box">{result["summary"]}</div>', unsafe_allow_html=True)
    if requirements:
        st.caption("我已按以下条件筛选：" + "；".join(requirements))

    product_card(selected, title="推荐商品", primary=True)
    with st.expander("查看备选商品与核验依据"):
        comparison = trace_item(trace, "candidate_comparison")
        eligible_count = comparison.get("eligible_product_count", 0)
        candidate_ids = [
            item_id
            for item_id in comparison.get("candidate_product_ids", [])
            if item_id != product_id and item_id in agent.repository.by_id
        ]
        # A small eligible set is worth showing in full; a large one only needs a
        # sample, since the guidance text already reports the full range.
        if eligible_count and eligible_count <= FEW_RESULTS_THRESHOLD:
            visible = candidate_ids
            heading = f"其余 {len(visible)} 件符合条件的商品"
        else:
            visible = candidate_ids[:2]
            heading = "可比较的备选商品"
        alternatives = [agent.repository.by_id[item_id] for item_id in visible]
        if alternatives:
            st.markdown(
                f'<div class="section-title" style="margin-top:1.2rem">{heading}</div>',
                unsafe_allow_html=True,
            )
            for row_start in range(0, len(alternatives), 3):
                row = alternatives[row_start : row_start + 3]
                columns = st.columns(len(row))
                for offset, (column, product) in enumerate(zip(columns, row)):
                    with column:
                        product_card(product, title=f"备选 {row_start + offset + 1}")
        show_decision_evidence(agent, result, include_trace_expander=False)


def show_bundle_recommendation(agent: Agent, result: dict[str, Any]) -> None:
    """Render a verified multi-product set without treating it as an order."""
    data = result.get("catalog_data", {})
    bundle = data.get("bundle", {})
    quantities_by_product_id: dict[str, int] = {}
    for item in bundle.get("items", []):
        product_id = str(item.get("product", {}).get("product_id", "")).strip()
        if not product_id:
            continue
        quantities_by_product_id[product_id] = quantities_by_product_id.get(product_id, 0) + int(
            item.get("quantity", 1)
        )
    products = []
    seen_product_ids: set[str] = set()
    for item in data.get("products", []):
        product_id = str(item.get("product_id", "")).strip()
        if product_id in seen_product_ids or product_id not in agent.repository.by_id:
            continue
        seen_product_ids.add(product_id)
        products.append(agent.repository.by_id[product_id])

    st.markdown(f'<div class="summary-box">{result["summary"]}</div>', unsafe_allow_html=True)
    if not products:
        show_decision_evidence(agent, result)
        return

    total_price = bundle.get("total_price")
    if isinstance(total_price, (int, float)):
        st.caption(f"组合总价：USD {total_price:.2f}；当前结果仅用于商品推荐，不会创建订单。")

    search_strategy = bundle.get("search_strategy")
    if search_strategy:
        strategy_text = "穷举检索" if search_strategy == "exact_enumeration" else "有界候选搜索"
        st.caption(f"组合检索策略：{strategy_text}。完整核验记录可在下方展开查看。")

    columns = st.columns(len(products))
    for column, product in zip(columns, products):
        with column:
            quantity = quantities_by_product_id.get(product.product_id, 1)
            title = "组合商品" if quantity == 1 else f"组合商品 ×{quantity}"
            product_card(product, title=title, primary=True)
    with st.expander("查看组合检索与核验依据"):
        show_decision_evidence(agent, result, include_trace_expander=False)


def show_proactive_guidance(result: dict[str, Any]) -> None:
    """Expose verified follow-up directions without replacing free-form chat.

    The backend derives each phrase from the retrieved catalog.  Rendering them
    as optional natural-language examples preserves the chat-first interaction:
    users may type one verbatim, modify it, or ignore it entirely.
    """
    guidance = result.get("proactive_guidance")
    if not isinstance(guidance, dict):
        return
    message = guidance.get("message")
    examples = guidance.get("example_phrases", [])
    if not isinstance(message, str) or not message.strip():
        return
    example_html = "".join(
        f'<span class="guidance-example">{escape(str(phrase))}</span>'
        for phrase in examples
        if str(phrase).strip()
    )
    examples_html = (
        f'<div class="guidance-examples">例如：{example_html}</div>'
        if example_html
        else ""
    )
    st.markdown(
        f'<div class="guidance-box"><strong>下一步可以这样继续：</strong> '
        f'{escape(message)}{examples_html}</div>',
        unsafe_allow_html=True,
    )


def show_catalog_answer(agent: Agent, result: dict[str, Any]) -> None:
    """Keep catalog facts concise while making returned products inspectable."""
    st.info(result.get("summary", "已完成商品库查询。"))
    data = result.get("catalog_data", {})
    kind = data.get("kind")
    facets = data.get("facets", {})
    if facets:
        facet_names = {"item_type": "商品类型", "manufacturer": "厂商", "tag": "风格 / 标签"}
        for field, values in facets.items():
            if not values:
                continue
            rows = [{facet_names.get(field, field): value, "商品数量": count} for value, count in values.items()]
            with st.expander(f"查看{facet_names.get(field, field)}统计（{len(rows)} 项）", expanded=field == "tag"):
                st.dataframe(rows, hide_index=True, width="stretch")
    if kind == "catalog_overview":
        counts = data.get("type_counts", {})
        if counts:
            st.caption(" · ".join(f"{item_type}: {count} 件" for item_type, count in counts.items()))
        return

    if kind == "price_range":
        boundary_products = [data.get("lowest"), data.get("highest")]
        labels = ["最低价商品", "最高价商品"]
        columns = st.columns(2)
        for column, product_data, label in zip(columns, boundary_products, labels):
            if product_data:
                with column:
                    product_card(agent.repository.by_id[product_data["product_id"]], title=label)
        return

    if kind == "multi_type_price_range":
        rows = [
            {
                "商品类型": item["item_type"],
                "商品数量": item["count"],
                "最低价": f'${item["lowest"]["price"]:.2f}',
                "最低价商品": item["lowest"]["product_id"],
                "最高价": f'${item["highest"]["price"]:.2f}',
                "最高价商品": item["highest"]["product_id"],
            }
            for item in data.get("price_ranges", [])
        ]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")
        return

    if kind == "exploration":
        products = data.get("products", [])
        if products:
            st.caption(f"先展示 {len(products)} 件目录样例；它们不是最终推荐。")
            columns = st.columns(len(products))
            for index, (column, product_data) in enumerate(zip(columns, products), start=1):
                with column:
                    product_card(agent.repository.by_id[product_data["product_id"]], title=f"目录样例 {index}")
        return

    products = data.get("products", [])
    if kind == "product_list" and products:
        rows = [
            {
                "商品 ID": product["product_id"],
                "名称": product["name"],
                "价格": f'${product["price"]:.2f}',
                "厂商": product["manufacturer"],
                "标签": "、".join(product["tags"]),
            }
            for product in products
        ]
        st.dataframe(rows, hide_index=True, width="stretch")
        return

    if products:
        if kind == "product_comparison" and len(products) > 2:
            rows = [
                {
                    "商品 ID": product["product_id"],
                    "名称": product["name"],
                    "类别": product["item_type"],
                    "价格": f'${product["price"]:.2f}',
                    "厂商": product["manufacturer"],
                    "标签": "、".join(product["tags"]),
                }
                for product in products
            ]
            st.dataframe(rows, hide_index=True, width="stretch")
            return
        columns = st.columns(min(2, len(products)))
        for index, (column, product_data) in enumerate(zip(columns, products), start=1):
            with column:
                title = "商品详情" if kind == "product_detail" else f"商品 {index}"
                product_card(agent.repository.by_id[product_data["product_id"]], title=title)


def show_local_collection(agent: Agent, result: dict[str, Any]) -> None:
    """Render session-local favorites and mock orders with an explicit boundary."""
    data = result.get("catalog_data", {})
    kind = data.get("kind")
    if kind == "simulated_order":
        order = data.get("order", {})
        status = order.get("status")
        if status == "cancelled_local":
            st.warning(result.get("summary", "已更新本地模拟订单。"))
        else:
            st.success(result.get("summary", "已创建本地模拟订单。"))
        if order:
            st.caption(
                f"订单编号：{order.get('order_id', '-')} · 状态：{status or '-'} · "
                f"总价：USD {float(order.get('total_price', 0)):.2f} · 仅本地模拟"
            )
    else:
        st.info(result.get("summary", "已更新当前会话收藏。"))

    products = [
        agent.repository.by_id[item["product_id"]]
        for item in data.get("products", [])
        if item.get("product_id") in agent.repository.by_id
    ]
    if products:
        columns = st.columns(min(3, len(products)))
        title = "已收藏商品" if kind in {"favorites", "favorite_saved"} else "模拟订单商品"
        for column, product in zip(columns, products):
            with column:
                product_card(product, title=title, primary=kind == "simulated_order")

    if kind == "simulated_order_list":
        rows = [
            {
                "订单编号": order.get("order_id", "-"),
                "商品": "、".join(
                    f'{line.get("product_id", "-")} ×{int(line.get("quantity", 1))}'
                    for line in order.get("line_items", [])
                    if isinstance(line, dict)
                ) or "、".join(order.get("product_ids", [])),
                "总价": f"${float(order.get('total_price', 0)):.2f}",
                "状态": order.get("status", "-"),
            }
            for order in data.get("orders", [])
        ]
        if rows:
            st.dataframe(rows, hide_index=True, width="stretch")


def render_assistant_result(agent: Agent, result: dict[str, Any]) -> None:
    """Render one completed assistant turn in either the history or live slot."""
    response_type = result.get("response_type")
    if response_type == "conflict":
        st.warning(result.get("summary", "请确认需求。"))
    elif response_type == "capability_unavailable":
        st.warning(result.get("summary", "当前系统暂不支持该操作。"))
    elif response_type == "service_error":
        st.error(result.get("summary", "模型服务暂不可用，请稍后重试。"))
        failed = next(
            (step for step in result.get("trace", []) if step.get("status") == "failed"),
            {},
        )
        if failed:
            st.caption(
                "失败阶段："
                + str(failed.get("step", "unknown"))
                + "；错误类别："
                + str(failed.get("error_code", "model_response_error"))
            )
    elif response_type in {"catalog_query", "product_detail", "product_comparison", "exploration"}:
        show_catalog_answer(agent, result)
    elif response_type == "bundle_recommendation":
        show_bundle_recommendation(agent, result)
    elif response_type == "local_collection":
        show_local_collection(agent, result)
    elif result.get("purchased_product_id") is None:
        st.info(result.get("summary", "请补充需求。"))
        if response_type == "no_match":
            show_closest_alternatives(agent, result)
    else:
        show_recommendation(agent, result)
    show_proactive_guidance(result)


def render_conversation(agent: Agent, state: ConversationState) -> None:
    for event in state.events:
        if event.event_type == "user_message":
            with st.chat_message("user"):
                st.write(event.payload.get("message", ""))
        elif event.event_type == "assistant_message":
            with st.chat_message("assistant"):
                render_assistant_result(agent, event.payload.get("result", {}))


def recommendation_page(agent: Agent) -> None:
    if "conversation_state" not in st.session_state:
        conversation_id = str(st.query_params.get("conversation", "")).strip()
        try:
            state = ConversationState(conversation_id=conversation_id) if conversation_id else ConversationState()
            restore_status = agent.restore_local_session(state).get("status")
        except ValueError:
            state = ConversationState()
            restore_status = "invalid_id"
        st.session_state.conversation_state = state
        if restore_status == "loaded":
            st.session_state.local_session_restored = True
    state: ConversationState = st.session_state.conversation_state
    st.query_params["conversation"] = state.conversation_id

    title_column, action_column = st.columns([0.8, 0.2])
    with title_column:
        st.markdown('<div class="section-title">💬 购物对话</div>', unsafe_allow_html=True)
        if st.session_state.pop("local_session_restored", False):
            st.caption("已恢复此设备上的会话收藏、模拟订单和排序偏好；聊天原文不会写入本地文件。")
    with action_column:
        if st.button("新建对话", width="stretch"):
            st.session_state.conversation_state = ConversationState()
            st.query_params["conversation"] = st.session_state.conversation_state.conversation_id
            st.rerun()

    render_conversation(agent, state)
    submitted_message = st.chat_input("输入购物需求（如：马克杯、预算 15 以内、Ocean 主题）")
    if submitted_message:
        # Render the current turn immediately.  The persistent event log is
        # already updated by run_turn, so a later interaction can redraw the
        # same completed turn from history without needing an extra rerun here.
        with st.chat_message("user"):
            st.write(submitted_message)
        with st.chat_message("assistant"):
            with st.spinner("正在分析需求…"):
                result = agent.run_turn(submitted_message, state)
            render_assistant_result(agent, result)


def catalog_page(agent: Agent) -> None:
    st.markdown('<div class="section-title">商品库</div>', unsafe_allow_html=True)
    catalog = agent.repository.catalog()
    first, second = st.columns([0.72, 0.28])
    with first:
        keyword = st.text_input("搜索", placeholder="搜索名称、厂商、标签或描述")
    with second:
        item_type = st.selectbox("商品类型", ["全部", *catalog["item_types"]])

    key = keyword.casefold().strip()
    products = [
        product for product in agent.repository.products
        if (item_type == "全部" or product.item_type == item_type)
        and (
            not key
            or key in " ".join(
                [product.name, product.manufacturer, product.description, *product.tags]
            ).casefold()
        )
    ]
    products.sort(key=lambda product: (product.price, product.name))
    total_pages = max(1, (len(products) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = st.number_input("页码", min_value=1, max_value=total_pages, value=1, step=1)
    start = (page - 1) * PAGE_SIZE
    rows = [
        {
            "商品 ID": product.product_id,
            "名称": product.name,
            "类别": product.item_type,
            "厂商": product.manufacturer,
            "价格": f"${product.price:.2f}",
            "标签": "、".join(product.tags),
            "商品描述": product.description,
        }
        for product in products[start : start + PAGE_SIZE]
    ]
    st.caption(f"共 {len(products)} 件商品 · 第 {page}/{total_pages} 页")
    st.dataframe(rows, hide_index=True, width="stretch")


def main() -> None:
    st.set_page_config(page_title="智能购物 Agent", page_icon="🛍️", layout="wide")
    apply_style()
    agent = get_agent(AGENT_IMPLEMENTATION_VERSION)
    st.markdown(
        """
        <div class="hero">
          <h1>智能购物Agent</h1>
        </div>
        """,
        unsafe_allow_html=True,
    )
    recommendation_tab, catalog_tab = st.tabs(["🛍️ 智能推荐", "🗂️ 商品库浏览"])
    with recommendation_tab:
        recommendation_page(agent)
    with catalog_tab:
        catalog_page(agent)


if __name__ == "__main__":
    main()

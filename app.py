from __future__ import annotations

from pathlib import Path
from typing import Any

import streamlit as st

from starter.agent_interface import Agent, ConversationState


PROJECT_DIR = Path(__file__).resolve().parent
DATA_DIR = PROJECT_DIR / "data"
PAGE_SIZE = 12
AGENT_IMPLEMENTATION_VERSION = str((PROJECT_DIR / "starter" / "agent_interface.py").stat().st_mtime_ns)
def apply_style() -> None:
    st.markdown(
        """
        <style>
          .stApp { background: #f5f8fc; color: #17385f; }
          header[data-testid="stHeader"] { background: rgba(245,248,252,.94); }
          .block-container { max-width: 1220px; padding-top: 1.35rem; padding-bottom: 3rem; }
          .hero { background: linear-gradient(110deg, #edf6ff, #ffffff 64%, #e0f0ff); border: 1px solid #c8def4; border-radius: 16px; padding: 22px 30px; margin-bottom: 1.1rem; }
          .hero h1 { color: #17549b; margin: 0; font-size: 2rem; }
          .hero p { color: #577693; margin: .4rem 0 0; }
          .section-title { border-left: 5px solid #2479d0; color: #1b5695; padding-left: 10px; font-size: 1.1rem; font-weight: 750; margin: .25rem 0 .85rem; }
          .result-card, .alternative-card { background: #ffffff; border: 1px solid #bed6ee; border-radius: 13px; padding: 16px 18px; box-shadow: 0 3px 13px rgba(25,86,157,.07); }
          .alternative-card { min-height: 188px; box-shadow: none; }
          .price { color: #1264b8; font-size: 1.55rem; font-weight: 750; margin: .05rem 0 .55rem; }
          .tag { display: inline-block; color: #2264a6; background: #eaf4ff; border: 1px solid #cce2f7; border-radius: 99px; padding: .16rem .5rem; margin: 0 .28rem .28rem 0; font-size: .82rem; }
          .muted { color: #6d8098; font-size: .9rem; }
          .summary-box { background: #eaf4ff; border-left: 4px solid #2376c9; border-radius: 6px; padding: .8rem 1rem; color: #174b83; margin-top: .85rem; }
          div[data-testid="stTextArea"] textarea { border: 1px solid #b8cde6; border-radius: 9px; }
          div[data-testid="stButton"] > button { background: #1d61aa; color: #fff; border: 1px solid #1d61aa; border-radius: 7px; font-weight: 600; }
          div[data-testid="stButton"] > button:hover { background: #174f8a; border-color: #174f8a; color: #fff; }
          button[data-baseweb="tab"] { color: #4a6d90; font-size: 1rem; font-weight: 650; }
          button[data-baseweb="tab"][aria-selected="true"] { color: #175aa1; }
          div[data-testid="stExpander"] { background: #fff; border: 1px solid #d4e2f0; border-radius: 9px; }
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
          <p class="muted" style="margin:.35rem 0 0">商品描述：{product.description}</p>
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
    if grounded.get("price_value") is not None:
        items.append(f"预算：价格 {grounded.get('price_operator')} ${grounded['price_value']:.2f}")
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


def show_decision_evidence(agent: Agent, result: dict[str, Any]) -> None:
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

    with st.expander("查看结构化核验记录"):
        safe_trace = []
        for item in trace:
            copied = dict(item)
            copied.pop("eligible_product_ids", None)
            safe_trace.append(copied)
        st.json(safe_trace, expanded=False)


def show_recommendation(agent: Agent, result: dict[str, Any]) -> None:
    product_id = result["purchased_product_id"]
    if product_id is None:
        st.warning(result["summary"])
        show_decision_evidence(agent, result)
        return

    selected = agent.repository.by_id[product_id]
    product_card(selected, title="推荐商品", primary=True)
    with st.expander("查看推荐理由、备选与核验依据"):
        st.markdown(f'<div class="summary-box">{friendly_summary(agent, result)}</div>', unsafe_allow_html=True)
        comparison = trace_item(result["trace"], "candidate_comparison")
        candidate_ids = comparison.get("candidate_product_ids", [])
        alternatives = [agent.repository.by_id[item_id] for item_id in candidate_ids if item_id != product_id and item_id in agent.repository.by_id][:2]
        if alternatives:
            st.markdown('<div class="section-title" style="margin-top:1.2rem">可比较的备选商品</div>', unsafe_allow_html=True)
            columns = st.columns(len(alternatives))
            for index, (column, product) in enumerate(zip(columns, alternatives), start=1):
                with column:
                    product_card(product, title=f"备选 {index}")
        show_decision_evidence(agent, result)


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


def render_conversation(agent: Agent, state: ConversationState) -> None:
    for event in state.events:
        if event.event_type == "user_message":
            with st.chat_message("user"):
                st.write(event.payload.get("message", ""))
        elif event.event_type == "assistant_message":
            result = event.payload.get("result", {})
            with st.chat_message("assistant"):
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
                elif response_type in {"catalog_query", "product_detail", "product_comparison"}:
                    show_catalog_answer(agent, result)
                elif result.get("purchased_product_id") is None:
                    st.info(result.get("summary", "请补充需求。"))
                else:
                    show_recommendation(agent, result)


def recommendation_page(agent: Agent) -> None:
    if "conversation_state" not in st.session_state:
        st.session_state.conversation_state = ConversationState()
    state: ConversationState = st.session_state.conversation_state

    title_column, action_column = st.columns([0.8, 0.2])
    with title_column:
        st.markdown('<div class="section-title">💬 购物对话</div>', unsafe_allow_html=True)
    with action_column:
        if st.button("新建对话", width="stretch"):
            st.session_state.conversation_state = ConversationState()
            st.rerun()

    render_conversation(agent, state)
    submitted_message = st.chat_input("输入购物需求，或回答上一个问题…")
    if submitted_message:
        with st.spinner("正在处理消息…"):
            agent.run_turn(submitted_message, state)
        st.rerun()


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

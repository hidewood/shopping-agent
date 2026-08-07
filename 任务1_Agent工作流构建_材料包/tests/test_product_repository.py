from __future__ import annotations

import unittest
import json
import re
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter.agent_interface import (
    CatalogConstraint,
    Concept,
    ConversationState,
    GroundedRequirement,
    LLMResponseError,
    PriceConstraint,
    ProductRepository,
    ShoppingAgent,
    ShoppingRequirement,
)


DATA_DIR = PROJECT_DIR / "data"


class StubLLM:
    def __init__(self, responses: list[dict]):
        self.responses = responses
        self.calls: list[list[dict]] = []

    def chat_json(self, _messages: list[dict]) -> dict:
        self.calls.append(_messages)
        if "single-turn planner" in _messages[0]["content"]:
            if self.responses and "workflow" in self.responses[0]:
                coordinator = self.responses.pop(0)
                if coordinator["workflow"] == "customer_chat":
                    return {
                        "intent": "chat",
                        "customer_reply": coordinator["customer_reply"],
                        "requirement": None,
                        "catalog_operations": [],
                        "state_action": "none",
                    }
                task = coordinator if "shopping_task" in coordinator else (
                    self.responses.pop(0) if self.responses and "shopping_task" in self.responses[0] else TestLLM.shopping_task(_messages)
                )
                if task["shopping_task"] == "recommendation":
                    requirement = self.responses.pop(0) if self.responses else TestLLM._requirement_from_messages(_messages)
                    return TestLLM.plan_from_requirement(requirement)
                if task["shopping_task"] == "catalog_query":
                    query = self.responses.pop(0) if self.responses else None
                    return TestLLM.plan_from_catalog_query(query, _messages)
                return TestLLM.plan_for_product_task(task["shopping_task"])
            if self.responses and ("item_type" in self.responses[0] or "concepts" in self.responses[0]):
                return TestLLM.plan_from_requirement(self.responses.pop(0))
            if self.responses and "query_kind" in self.responses[0]:
                return TestLLM.plan_from_catalog_query(self.responses.pop(0), _messages)
            if self.responses:
                return self.responses.pop(0)
            raise AssertionError("This StubLLM test must provide a plan-compatible response.")
        if "shopping-task router" in _messages[0]["content"] and (
            not self.responses or "shopping_task" not in self.responses[0]
        ):
            return TestLLM.shopping_task(_messages)
        return self.responses.pop(0)


class FailingLLM:
    def chat_json(self, _messages: list[dict]) -> dict:
        raise LLMResponseError("simulated timeout")


class TestLLM:
    """Deterministic API double used only to keep unit tests independent of a real API key."""

    def __init__(self, repository: ProductRepository):
        self.repository = repository

    @staticmethod
    def _message(messages: list[dict]) -> str:
        payload = json.loads(messages[-1]["content"])
        return payload.get("latest_user_message") or payload.get("user_request") or ""

    @staticmethod
    def shopping_task(messages: list[dict]) -> dict:
        message = TestLLM._message(messages)
        lower = message.casefold()
        product_ids = ShoppingAgent._product_ids_in_message(message)
        if len(product_ids) >= 2 and any(word in lower for word in ("比较", "compare", "difference")):
            return {"shopping_task": "product_comparison"}
        if product_ids and any(word in lower for word in ("详情", "描述", "标签", "介绍", "detail", "description")):
            return {"shopping_task": "product_detail"}
        catalog_signals = (
            "有吗", "有哪些", "都有什么", "什么商品", "价位", "价格范围", "多少钱", "最便宜",
            "最低价", "最贵", "最高价", "多少件", "多少种", "catalog", "price range", "cheapest",
            "most expensive", "how many",
        )
        return {"shopping_task": "catalog_query" if any(word in lower for word in catalog_signals) else "recommendation"}

    @staticmethod
    def _requirement_from_messages(messages: list[dict]) -> dict:
        raise AssertionError("A requirement response must be supplied by this stub.")

    @staticmethod
    def plan_from_requirement(requirement: dict) -> dict:
        return {
            "intent": "recommendation",
            "customer_reply": None,
            "requirement": requirement,
            "catalog_operations": [],
            "state_action": "merge",
        }

    @staticmethod
    def plan_for_product_task(task: str) -> dict:
        return {
            "intent": task,
            "customer_reply": None,
            "requirement": None,
            "catalog_operations": [],
            "state_action": "none",
        }

    @staticmethod
    def plan_from_catalog_query(query: dict | None, messages: list[dict]) -> dict:
        if query is None:
            raise AssertionError("A catalog response must be supplied by this stub.")
        operations = {
            "catalog_overview": ["count", "group_by_item_type"],
            "price_range": ["price_range"],
            "product_list": ["list"],
            "price_extreme": ["price_extreme"],
        }[query["query_kind"]]
        return {
            "intent": "catalog",
            "customer_reply": None,
            "requirement": query["filters"],
            "catalog_operations": operations,
            "state_action": "none",
        }

    def plan(self, messages: list[dict]) -> dict:
        message = TestLLM._message(messages)
        lower = message.casefold()
        if any(word in lower for word in ("你好", "hello", "天气")):
            reply = "我是智能购物 Agent，可以协助商品挑选与推荐。"
            if "天气" in lower:
                reply = "很抱歉，我是购物助手，无法提供天气信息。请问有什么购物方面可以帮您？"
            return {"intent": "chat", "customer_reply": reply, "requirement": None, "catalog_operations": [], "state_action": "none"}
        task = TestLLM.shopping_task(messages)["shopping_task"]
        if task in {"product_detail", "product_comparison"}:
            return TestLLM.plan_for_product_task(task)
        requirement = self._requirement(message)
        if task == "recommendation":
            return TestLLM.plan_from_requirement(requirement)
        if any(token in lower for token in ("风格", "标签", "style", "tag")):
            operations = ["count", "group_by_tag"]
        elif any(token in lower for token in ("价位", "价格范围", "多少钱", "price range")):
            operations = ["price_range"]
        elif any(token in lower for token in ("最便宜", "最低价", "最贵", "最高价", "cheapest", "most expensive")):
            operations = ["price_extreme"]
        elif any(token in lower for token in ("哪些", "什么商品", "有什么", "有吗", "列表", "list")):
            operations = ["list"]
        else:
            operations = ["count", "group_by_item_type"]
        requirement["needs_clarification"] = False
        requirement["clarification_question"] = None
        return {"intent": "catalog", "customer_reply": None, "requirement": requirement, "catalog_operations": operations, "state_action": "none"}

    def chat_json(self, messages: list[dict]) -> dict:
        system = messages[0]["content"]
        message = self._message(messages)
        lower = message.casefold()
        if "conversation coordinator" in system:
            if any(word in lower for word in ("你好", "hello", "天气")):
                reply = "我是智能购物 Agent，可以协助商品挑选与推荐。"
                if "天气" in lower:
                    reply = "很抱歉，我是购物助手，无法提供天气信息。请问有什么购物方面可以帮您？"
                return {"workflow": "customer_chat", "customer_reply": reply}
            return {"workflow": "shopping_request", "customer_reply": None}
        if "single-turn planner" in system:
            return self.plan(messages)
        if "shopping-task router" in system:
            return self.shopping_task(messages)
        if "catalog-query analysis worker" in system:
            filters = self._requirement(message)
            lower = message.casefold()
            if any(token in lower for token in ("价位", "价格范围", "多少钱", "price range")):
                kind = "price_range"
            elif any(token in lower for token in ("最便宜", "最低价", "最贵", "最高价", "cheapest", "most expensive")):
                kind = "price_extreme"
            elif filters["item_type"]["raw_value"] and any(
                token in lower for token in ("哪些", "什么商品", "有什么", "有吗", "列表", "list")
            ):
                kind = "product_list"
            else:
                kind = "catalog_overview"
            filters["needs_clarification"] = False
            filters["clarification_question"] = None
            return {"query_kind": kind, "filters": filters}
        if "requirement-analysis worker" in system:
            return self._requirement(message)
        if "product decision component" in system:
            payload = json.loads(messages[-1]["content"])
            product = payload["candidates"][0]
            is_unverified_style = "disney-style" in payload["user_request"].casefold()
            return {
                "purchased_product_id": product["product_id"],
                "reason": (
                    "无法验证未映射的风格偏好，以下商品仅保证硬条件。"
                    if is_unverified_style
                    else "测试模型选择了已验证候选。"
                ),
                "tradeoffs": [],
                "confidence": "high",
                "match_level": "closest_alternative" if is_unverified_style else "exact_match",
            }
        raise AssertionError("Unexpected test-model prompt")

    def _requirement(self, message: str) -> dict:
        """Small test-double parser; production parsing always calls the model API."""
        lower = message.casefold()
        price = ShoppingAgent._price_constraint_from_instruction(message)
        catalog = self.repository.catalog()
        item_type = next(
            (item for item in catalog["item_types"] if re.search(rf"\b{re.escape(item)}\b", lower)),
            None,
        )
        if item_type is None:
            aliases = {
                "mug": ("马克杯", "杯子", "咖啡杯", "水杯"),
                "shirt": ("T恤", "t恤", "体恤", "衬衫", "上衣"),
            }
            item_type = next(
                (canonical for canonical, values in aliases.items() if any(value.casefold() in lower for value in values)),
                None,
            )
        unknown_type = None
        if item_type is None:
            match = re.search(r"\b(?:need|want|buy|find|looking\s+for)\s+(?:an?\s+|the\s+)?([a-z][\w-]*)", lower)
            if match and match.group(1) not in {"affordable", "best", "gift", "present", "product", "item", "something"}:
                unknown_type = match.group(1)
        manufacturer = next(
            (value for value in catalog["manufacturers"] if value.casefold() in lower), None
        )
        hard_text, *preference_parts = re.split(r"\bprefer(?:red)?\b|优先", message, maxsplit=1, flags=re.IGNORECASE)
        preference_text = preference_parts[0] if preference_parts else ""
        hard_tags = self.repository.tags_in_text(hard_text)
        preferred_tags = self.repository.tags_in_text(preference_text)
        concepts = [
            {
                "raw_value": tag,
                "kind": "theme",
                "constraint_strength": "hard",
                "catalog_tag_hints": [tag],
            }
            for tag in hard_tags
        ] + [
            {
                "raw_value": tag,
                "kind": "theme",
                "constraint_strength": "preference",
                "catalog_tag_hints": [tag],
            }
            for tag in preferred_tags
            if tag not in hard_tags
        ]
        official = re.search(r"\b(?:official|licensed|authentic)\s+([a-z0-9][\w-]*)\b", lower)
        if official:
            concepts.append(
                {
                    "raw_value": official.group(1),
                    "kind": "brand",
                    "constraint_strength": "hard",
                    "catalog_tag_hints": [],
                }
            )
        style = re.search(r"\b([a-z0-9][\w-]*)[- ]style\b", lower)
        if style:
            concepts.append(
                {
                    "raw_value": f"{style.group(1)} style",
                    "kind": "style",
                    "constraint_strength": "preference",
                    "catalog_tag_hints": [],
                }
            )
        raw_type = item_type or unknown_type
        has_constraint = any([raw_type, manufacturer, price.value, concepts])
        needs_detail = bool(raw_type and not manufacturer and price.value is None and not concepts)
        return {
            "item_type": {
                "raw_value": raw_type,
                "constraint_strength": "hard",
                "catalog_hint": item_type,
            },
            "manufacturer": {
                "raw_value": manufacturer,
                "constraint_strength": "preference" if manufacturer and manufacturer.casefold() in preference_text.casefold() else "hard",
                "catalog_hint": manufacturer,
            },
            "price_constraint": {"operator": price.operator, "value": price.value} if price.value is not None else None,
            "concepts": concepts,
            "needs_clarification": not has_constraint or needs_detail,
            "clarification_question": (
                "请补充你最看重的商品类型、预算或需求特点。"
                if not has_constraint
                else "请补充预算、喜欢的主题或品牌；也可以直接说“没有特别要求”。"
                if needs_detail
                else None
            ),
        }



class ProductRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = ProductRepository(DATA_DIR)

    def test_loads_all_products_and_catalog(self) -> None:
        self.assertEqual(len(self.repository.products), 1740)
        self.assertEqual(self.repository.catalog()["item_types"], ["mug", "shirt"])
        self.assertIn("Clothes", self.repository.catalog()["tags"])

    def test_catalog_grounding_maps_clothes_themed_to_clothes(self) -> None:
        requirement = ShoppingRequirement(
            item_type=CatalogConstraint("shirt", "hard", "shirt"),
            price_constraint=PriceConstraint("<=", 23),
            concepts=[
                Concept(
                    raw_value="Clothes themed",
                    kind="theme",
                    constraint_strength="hard",
                    catalog_tag_hints=["Clothes"],
                )
            ],
        )
        grounded = self.repository.ground(requirement)
        products, counts = self.repository.retrieve(grounded)
        self.assertEqual(grounded.required_tags, ["Clothes"])
        self.assertEqual(grounded.unresolved_hard_constraints, [])
        self.assertGreater(counts["after_required_tags"], 0)
        self.assertEqual(products[0].item_type, "shirt")
        self.assertIn("Clothes", products[0].tags)
        self.assertLess(products[0].price, 23)

    def test_bilingual_tag_aliases_map_only_to_real_catalog_tags(self) -> None:
        self.assertIn("Ocean", self.repository.tags_in_text("我想看海洋主题的商品"))
        self.assertIn("Sunset", self.repository.tags_in_text("我喜欢夕阳图案"))
        self.assertIn("Clothes", self.repository.tags_in_text("服装主题"))
        self.assertNotIn("Disney", self.repository.tags_in_text("迪士尼风格"))

    def test_api_stub_chinese_aliases_drive_verified_retrieval(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run_turn("我想买一个海洋主题的马克杯，预算低于 30", ConversationState())
        grounding = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        selected = agent.repository.by_id[result["purchased_product_id"]]
        self.assertEqual(result["response_type"], "recommendation")
        self.assertEqual(grounding["grounded_requirements"]["item_type"], "mug")
        self.assertEqual(grounding["grounded_requirements"]["required_tags"], ["Ocean"])
        self.assertIn("Ocean", selected.tags)

    def test_chinese_preference_alias_remains_a_soft_preference(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run_turn("我想买一个马克杯，预算低于 30，优先海洋主题", ConversationState())
        grounding = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertEqual(grounding["grounded_requirements"]["required_tags"], [])
        self.assertEqual(grounding["grounded_requirements"]["preferred_tags"], ["Ocean"])

    def test_unavailable_hard_category_does_not_silently_substitute(self) -> None:
        requirement = ShoppingRequirement(
            item_type=CatalogConstraint("camera", "hard", None),
            price_constraint=PriceConstraint("<=", 100),
        )
        grounded = self.repository.ground(requirement)
        products, _ = self.repository.retrieve(grounded)
        self.assertEqual(products, [])
        self.assertTrue(grounded.unresolved_hard_constraints)

    def test_api_stub_preserves_unknown_requested_product_type(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run("I need a camera under $100.")
        self.assertIsNone(result["purchased_product_id"])
        grounding = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertTrue(grounding["grounded_requirements"]["unresolved_hard_constraints"])

    def test_api_stub_preserves_unknown_official_brand(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run("I must buy an official Disney shirt under $30.")
        self.assertIsNone(result["purchased_product_id"])
        grounding = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertTrue(grounding["grounded_requirements"]["unresolved_hard_constraints"])

    def test_api_stub_does_not_claim_an_unverified_style_match(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run("I want a Disney-style shirt under $30.")
        self.assertIsNotNone(result["purchased_product_id"])
        self.assertIn("无法验证未映射的风格", result["summary"])

    def test_preferred_manufacturer_is_not_a_hard_filter(self) -> None:
        requirement = ShoppingRequirement(
            item_type=CatalogConstraint("mug", "hard", "mug"),
            manufacturer=CatalogConstraint("Bayer-and-Sons", "preference", "Bayer-and-Sons"),
            price_constraint=PriceConstraint("<=", 100),
        )
        grounded = self.repository.ground(requirement)
        products, _ = self.repository.retrieve(grounded)
        self.assertGreater(len(products), 1)
        self.assertEqual(products[0].manufacturer, "Bayer-and-Sons")

    def test_agent_uses_grounded_values_with_stubbed_model(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "item_type": {
                        "raw_value": "shirt",
                        "constraint_strength": "hard",
                        "catalog_hint": "shirt",
                    },
                    "manufacturer": {
                        "raw_value": None,
                        "constraint_strength": "hard",
                        "catalog_hint": None,
                    },
                    "price_constraint": {"operator": "<", "value": 23},
                    "concepts": [
                        {
                            "raw_value": "Clothes themed",
                            "kind": "theme",
                            "constraint_strength": "hard",
                            "catalog_tag_hints": ["Clothes"],
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {
                    "purchased_product_id": "P1635",
                    "reason": "The product matches the grounded Clothes tag and budget.",
                    "tradeoffs": [],
                    "confidence": "high",
                    "match_level": "exact_match",
                },
            ]
        )
        result = agent.run("I need a Clothes themed shirt that costs less than $23.")
        selected = agent.repository.by_id[result["purchased_product_id"]]
        self.assertIn("Clothes", selected.tags)
        grounding = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertEqual(grounding["grounded_requirements"]["required_tags"], ["Clothes"])

    def test_lower_price_wins_when_soft_preference_scores_tie(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "item_type": {
                        "raw_value": "mug",
                        "constraint_strength": "hard",
                        "catalog_hint": "mug",
                    },
                    "manufacturer": {
                        "raw_value": None,
                        "constraint_strength": "hard",
                        "catalog_hint": None,
                    },
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [
                        {
                            "raw_value": "Ocean",
                            "kind": "theme",
                            "constraint_strength": "preference",
                            "catalog_tag_hints": ["Ocean"],
                        }
                    ],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {
                    "purchased_product_id": "P0004",
                    "reason": "The name directly mentions Ocean.",
                    "tradeoffs": [],
                    "confidence": "high",
                    "match_level": "exact_match",
                },
            ]
        )
        result = agent.run("I need a mug under $30; prefer Ocean themed products.")
        self.assertEqual(result["purchased_product_id"], "P0005")
        validation = next(step for step in result["trace"] if step["step"] == "decision_validation")
        self.assertEqual(validation["status"], "corrected")
        self.assertEqual(validation["selected_product_id"], "P0005")

    def test_agent_returns_starter_contract_with_api_stub(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run("Find a shirt about Barn from Konopelski-Inc with price under $17.")
        self.assertIsNotNone(result["purchased_product_id"])
        self.assertEqual(set(result), {"instruction", "purchased_product_id", "trace", "summary"})
        self.assertIsInstance(result["trace"], list)

    def test_incomplete_price_is_not_treated_as_no_budget(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run("I need a shirt that costs less than .")
        self.assertIsNone(result["purchased_product_id"])
        self.assertIn("input_integrity", [step["step"] for step in result["trace"]])

    def test_unavailable_api_does_not_trigger_a_local_recommendation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = None
        agent._settings_error = "simulated missing API"
        result = agent.run_turn("I need a mug under $30.", ConversationState())
        self.assertEqual(result["response_type"], "service_error")
        self.assertIsNone(result["purchased_product_id"])
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_null_catalog_concepts_are_normalized_without_crashing(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "catalog_query"},
                {
                    "query_kind": "catalog_overview",
                    "filters": {
                        "item_type": None,
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": None,
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        result = agent.run_turn("你家都有什么商品？", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["total_count"], 1740)

    def test_later_hard_theme_replaces_an_earlier_soft_preference(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "recommendation"},
                {
                    "item_type": {"raw_value": "mug", "constraint_strength": "hard", "catalog_hint": "mug"},
                    "manufacturer": None,
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [{"raw_value": "Ocean", "kind": "theme", "constraint_strength": "preference", "catalog_tag_hints": ["Ocean"]}],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {"purchased_product_id": "P0005", "reason": "符合条件。", "tradeoffs": [], "confidence": "high", "match_level": "exact_match"},
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "recommendation"},
                {
                    "item_type": None,
                    "manufacturer": None,
                    "price_constraint": None,
                    "concepts": [{"raw_value": "Ocean", "kind": "theme", "constraint_strength": "hard", "catalog_tag_hints": ["Ocean"]}],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {"purchased_product_id": "P0005", "reason": "符合条件。", "tradeoffs": [], "confidence": "high", "match_level": "exact_match"},
            ]
        )
        state = ConversationState()
        agent.run_turn("I need a mug under $30; prefer Ocean", state)
        result = agent.run_turn("Ocean is mandatory", state)
        grounded = next(step for step in result["trace"] if step["step"] == "catalog_grounding")
        self.assertEqual(grounded["grounded_requirements"]["required_tags"], ["Ocean"])
        self.assertEqual(grounded["grounded_requirements"]["preferred_tags"], [])

    def test_invalid_model_decision_returns_service_error_and_rolls_back_turn_state(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "recommendation"},
                {
                    "item_type": {"raw_value": "mug", "constraint_strength": "hard", "catalog_hint": "mug"},
                    "manufacturer": None,
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {"purchased_product_id": "P9999", "reason": "无效 ID。", "tradeoffs": [], "confidence": "high", "match_level": "exact_match"},
            ]
        )
        state = ConversationState()
        result = agent.run_turn("I need a mug under $30", state)
        self.assertEqual(result["response_type"], "service_error")
        self.assertIsNone(result["purchased_product_id"])
        self.assertFalse(any(event.event_type == "constraint_update" for event in state.events))
        self.assertEqual(agent._reduce_requirement(state).item_type.raw_value, None)
        self.assertIn("decision_validation", [step["step"] for step in result["trace"]])
        self.assertEqual(result["trace"][-1]["step"], "model_service")

    def test_strict_price_operator_is_resolved_by_code(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        requirement = ShoppingRequirement(price_constraint=PriceConstraint("<=", 30))
        trace: list[dict] = []
        agent._resolve_price_constraint("I need a mug under $30.", requirement, trace)
        self.assertEqual(requirement.price_constraint, PriceConstraint("<", 30))
        self.assertEqual(trace[0]["operator"], "<")

    def test_api_requirement_stub_keeps_main_theme_hard(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        requirement = ShoppingRequirement.from_dict(
            TestLLM(agent.repository)._requirement(
                "Buy an affordable mug related to Sunny; prefer Bayer-and-Sons if available."
            )
        )
        grounded = self.repository.ground(requirement)
        self.assertEqual(grounded.required_tags, ["Sunny"])
        self.assertEqual(grounded.preferred_tags, [])
        self.assertEqual(grounded.preferred_manufacturer, "Bayer-and-Sons")

    def test_decision_failure_is_recorded_once(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = FailingLLM()
        requirement = GroundedRequirement(required_tags=["Nature"])
        candidates, _ = self.repository.retrieve(requirement)
        trace: list[dict] = []
        with self.assertRaises(LLMResponseError):
            agent._make_decision("Find a Nature mug.", requirement, candidates, trace)
        comparisons = [step for step in trace if step["step"] == "candidate_comparison"]
        self.assertEqual(len(comparisons), 1)
        self.assertEqual(comparisons[0]["handler"], "deepseek")
        self.assertEqual(comparisons[0]["status"], "failed")
        self.assertEqual(
            comparisons[0]["candidate_product_ids"],
            [candidate.product_id for candidate in candidates],
        )

    def test_primary_topic_is_promoted_to_hard_constraint(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        requirement = ShoppingRequirement(
            concepts=[
                Concept(
                    raw_value="Beach",
                    kind="theme",
                    constraint_strength="preference",
                    catalog_tag_hints=["Beach"],
                )
            ]
        )
        trace: list[dict] = []
        agent._enforce_primary_topic_constraints(
            "Find a mug about Beach; prefer Bayer-and-Sons if available.", requirement, trace
        )
        self.assertEqual(requirement.concepts[0].constraint_strength, "hard")
        self.assertEqual(trace[0]["promoted_to_hard_tags"], ["Beach"])

    def test_multiturn_clarification_then_recommendation_reuses_state(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        first = agent.run_turn("I want a gift.", state)
        second = agent.run_turn(
            "A mug under $30; prefer Ocean themed products.", state
        )
        self.assertEqual(first["response_type"], "clarification")
        self.assertEqual(first["conversation_state"]["pending_fields"], ["item_type"])
        self.assertEqual(second["response_type"], "recommendation")
        self.assertEqual(second["purchased_product_id"], "P0005")
        self.assertEqual(state.status, "recommendation")
        self.assertGreaterEqual(
            sum(event.event_type == "constraint_update" for event in state.events), 3
        )

    def test_multiturn_budget_replaces_previous_value(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        agent.run_turn("I need a mug under $30.", state)
        result = agent.run_turn("Actually, under $8.", state)
        active = agent._reduce_requirement(state)
        self.assertEqual(result["response_type"], "no_match")
        self.assertEqual(active.item_type.raw_value, "mug")
        self.assertEqual(active.price_constraint, PriceConstraint("<", 8))

    def test_multiturn_conflicting_types_require_user_choice(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        result = agent.run_turn("I need a mug and a shirt.", state)
        self.assertEqual(result["response_type"], "conflict")
        self.assertIsNone(result["purchased_product_id"])
        self.assertEqual(state.pending_fields, ["item_type"])

    def test_type_change_requires_override_then_updates_state(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        agent.run_turn("I need a mug under $30.", state)
        conflict = agent.run_turn("I need a shirt.", state)
        updated = agent.run_turn("Actually, change to a shirt under $30.", state)
        active = agent._reduce_requirement(state)
        self.assertEqual(conflict["response_type"], "conflict")
        self.assertEqual(updated["response_type"], "recommendation")
        self.assertEqual(active.item_type.raw_value, "shirt")

    def test_multiturn_states_do_not_leak_between_users(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        first_state = ConversationState()
        second_state = ConversationState()
        agent.run_turn("I need a mug under $30.", first_state)
        second_result = agent.run_turn("I want a gift.", second_state)
        self.assertEqual(agent._reduce_requirement(first_state).item_type.raw_value, "mug")
        self.assertEqual(second_result["response_type"], "clarification")
        self.assertIsNone(agent._reduce_requirement(second_state).item_type.raw_value)

    def test_greeting_is_answered_without_entering_retrieval(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        result = agent.run_turn("你好", state)
        self.assertEqual(result["response_type"], "chat")
        self.assertIn("购物 Agent", result["summary"])
        self.assertFalse(
            any(event.event_type == "constraint_update" for event in state.events)
        )
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_chinese_type_only_request_asks_for_useful_detail(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run_turn("我想买一件T恤", ConversationState())
        self.assertEqual(result["response_type"], "clarification")
        self.assertIn("预算", result["summary"])

    def test_model_customer_service_route_returns_reply_without_retrieval(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "customer_chat",
                    "customer_reply": "你好！我可以帮你挑选商品。",
                }
            ]
        )
        result = agent.run_turn("你好", ConversationState())
        self.assertEqual(result["response_type"], "chat")
        self.assertEqual(result["summary"], "你好！我可以帮你挑选商品。")
        plan = next(step for step in result["trace"] if step["step"] == "turn_planning")
        self.assertEqual(plan["handler"], "deepseek")
        self.assertEqual(plan["intent"], "chat")
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_model_shopping_plan_contains_requirements_in_one_call(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "recommendation",
                    "customer_reply": None,
                },
                {
                    "item_type": {
                        "raw_value": "mug",
                        "constraint_strength": "hard",
                        "catalog_hint": "mug",
                    },
                    "manufacturer": {
                        "raw_value": None,
                        "constraint_strength": "hard",
                        "catalog_hint": None,
                    },
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {
                    "purchased_product_id": "P0005",
                    "reason": "It satisfies the type and budget.",
                    "tradeoffs": [],
                    "confidence": "high",
                    "match_level": "exact_match",
                },
            ]
        )
        result = agent.run_turn("I need a mug under $30.", ConversationState())
        self.assertEqual(result["response_type"], "recommendation")
        self.assertEqual(result["purchased_product_id"], "P0005")
        self.assertEqual([step["step"] for step in result["trace"]].count("turn_planning"), 1)
        self.assertEqual(len(agent.llm.calls), 2)  # one plan plus one candidate-comparison call

    def test_model_planned_clarification_is_shown_without_catalog_retrieval(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "recommendation",
                    "customer_reply": None,
                }
                ,
                {
                    "item_type": {
                        "raw_value": "shirt",
                        "constraint_strength": "hard",
                        "catalog_hint": "shirt",
                    },
                    "manufacturer": {
                        "raw_value": None,
                        "constraint_strength": "hard",
                        "catalog_hint": None,
                    },
                    "price_constraint": None,
                    "concepts": [],
                    "needs_clarification": True,
                    "clarification_question": "好的，我可以帮你挑选 T 恤。你的预算或喜欢的主题是什么？",
                },
            ]
        )
        result = agent.run_turn("我想买一件T恤", ConversationState())
        self.assertEqual(result["response_type"], "clarification")
        self.assertIn("预算", result["summary"])
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_customer_chat_does_not_overwrite_pending_shopping_state(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        state = ConversationState(
            pending_question="你的预算上限是多少？",
            pending_fields=["price_constraint"],
            status="awaiting_user",
        )
        agent.llm = StubLLM(
            [
                {
                    "workflow": "customer_chat",
                    "customer_reply": "你好！我可以协助你挑选商品。",
                },
            ]
        )
        result = agent.run_turn("你好", state)
        self.assertEqual(result["response_type"], "chat")
        self.assertEqual(result["summary"], "你好！我可以协助你挑选商品。")
        self.assertEqual(state.pending_question, "你的预算上限是多少？")
        self.assertEqual(state.pending_fields, ["price_constraint"])
        self.assertEqual(state.status, "awaiting_user")
        self.assertFalse(any(event.event_type == "constraint_update" for event in state.events))
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_invalid_coordinator_plan_returns_service_error(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "customer_reply": None,
                }
            ]
        )
        result = agent.run_turn("你好啊", ConversationState())
        self.assertEqual(result["response_type"], "service_error")
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_requirement_schema_cannot_be_mistaken_for_a_coordinator_plan(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"requirement": {"item_type": {"raw_value": "hello", "constraint_strength": "hard", "catalog_hint": None}}}
            ]
        )
        result = agent.run_turn("hello", ConversationState())
        self.assertEqual(result["response_type"], "service_error")
        self.assertFalse(
            any(step["step"] == "retrieval_and_hard_filtering" for step in result["trace"])
        )

    def test_catalog_price_query_is_not_mistaken_for_a_recommendation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        state = ConversationState(
            pending_question="你的预算上限是多少？",
            pending_fields=["price_constraint"],
            status="awaiting_user",
        )
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "catalog_query",
                    "customer_reply": None,
                },
                {
                    "query_kind": "price_range",
                    "filters": {
                        "item_type": {
                            "raw_value": "shirt",
                            "constraint_strength": "hard",
                            "catalog_hint": "shirt",
                        },
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        result = agent.run_turn("你家都有什么价位的衬衫", state)
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertIn("价格范围", result["summary"])
        self.assertEqual(result["catalog_data"]["kind"], "price_range")
        self.assertEqual(state.pending_fields, ["price_constraint"])
        self.assertEqual(state.status, "awaiting_user")
        self.assertFalse(any(event.event_type == "constraint_update" for event in state.events))

    def test_api_stub_catalog_price_query_covers_the_reported_dialogue_case(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run_turn("你家都有什么价位的衬衫", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertIn("价格范围", result["summary"])
        self.assertNotIn("请补充", result["summary"])

    def test_catalog_overview_returns_categories_instead_of_a_recommendation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "catalog_query",
                    "customer_reply": None,
                },
                {
                    "query_kind": "catalog_overview",
                    "filters": {
                        "item_type": None,
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        result = agent.run_turn("你们当前商品库有哪些商品类型？", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["kind"], "catalog_overview")
        self.assertEqual(result["catalog_data"]["type_counts"], {"mug": 870, "shirt": 870})

    def test_api_task_router_sends_general_catalog_question_to_catalog_query(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        stub = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "catalog_query"},
                {
                    "query_kind": "catalog_overview",
                    "filters": {
                        "item_type": None,
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        agent.llm = stub
        result = agent.run_turn("你家都有什么商品？", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["type_counts"], {"mug": 870, "shirt": 870})
        self.assertEqual(len(stub.calls), 1)
        self.assertEqual(
            next(step for step in result["trace"] if step["step"] == "turn_planning")["intent"],
            "catalog",
        )

    def test_api_task_router_treats_t_shirt_availability_as_catalog_fact(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "catalog_query"},
                {
                    "query_kind": "catalog_overview",
                    "filters": {
                        "item_type": {
                            "raw_value": "T恤",
                            "constraint_strength": "hard",
                            "catalog_hint": "shirt",
                        },
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        result = agent.run_turn("T恤有吗？", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["total_count"], 870)
        self.assertNotIn("预算", result["summary"])

    def test_compound_catalog_query_returns_verified_style_facets_without_state_mutation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState(
            pending_question="你的预算上限是多少？",
            pending_fields=["price_constraint"],
            status="awaiting_user",
        )
        result = agent.run_turn("你家有衬衫出售吗？都有哪些风格的衬衫呢？", state)
        facets = result["catalog_data"]["facets"]["tag"]
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertEqual(result["catalog_data"]["total_count"], 870)
        self.assertTrue(facets)
        self.assertTrue(set(facets).issubset(set(agent.repository.catalog()["tags"])))
        self.assertEqual(state.pending_fields, ["price_constraint"])
        self.assertFalse(any(event.event_type == "constraint_update" for event in state.events))

    def test_service_error_is_excluded_from_later_planning_context(self) -> None:
        class FailThenChat:
            def __init__(self) -> None:
                self.messages: list[list[dict]] = []

            def chat_json(self, messages: list[dict]) -> dict:
                self.messages.append(messages)
                if len(self.messages) == 1:
                    raise LLMResponseError("simulated timeout", error_code="timeout")
                context = json.loads(messages[-1]["content"])["recent_customer_messages"]
                self.context = context
                return {
                    "intent": "chat",
                    "customer_reply": "你好！我可以协助挑选商品。",
                    "requirement": None,
                    "catalog_operations": [],
                    "state_action": "none",
                }

        agent = ShoppingAgent(DATA_DIR)
        llm = FailThenChat()
        agent.llm = llm
        state = ConversationState()
        failed = agent.run_turn("推荐一件马克杯", state)
        recovered = agent.run_turn("你好", state)
        self.assertEqual(failed["response_type"], "service_error")
        self.assertEqual(failed["trace"][-1]["error_code"], "timeout")
        self.assertEqual(recovered["response_type"], "chat")
        self.assertFalse(
            any("模型服务暂不可用" in item["content"] for item in llm.context["recent_messages"])
        )

    def test_canonical_item_type_prevents_t_shirt_to_shirt_false_conflict(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "recommendation"},
                {
                    "item_type": {
                        "raw_value": "T恤",
                        "constraint_strength": "hard",
                        "catalog_hint": "shirt",
                    },
                    "manufacturer": None,
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {
                    "purchased_product_id": "P0888",
                    "reason": "符合条件。",
                    "tradeoffs": [],
                    "confidence": "high",
                    "match_level": "exact_match",
                },
                {"workflow": "shopping_request", "customer_reply": None},
                {"shopping_task": "recommendation"},
                {
                    "item_type": {
                        "raw_value": "shirt",
                        "constraint_strength": "hard",
                        "catalog_hint": "shirt",
                    },
                    "manufacturer": None,
                    "price_constraint": {"operator": "<", "value": 30},
                    "concepts": [],
                    "needs_clarification": False,
                    "clarification_question": None,
                },
                {
                    "purchased_product_id": "P0888",
                    "reason": "符合条件。",
                    "tradeoffs": [],
                    "confidence": "high",
                    "match_level": "exact_match",
                },
            ]
        )
        state = ConversationState()
        agent.run_turn("我想买 T恤，预算低于 30", state)
        result = agent.run_turn("我想买 shirt，预算低于 30", state)
        self.assertEqual(result["response_type"], "recommendation")
        self.assertFalse(any(step["step"] == "conflict_detection" for step in result["trace"]))

    def test_catalog_product_list_applies_catalog_filters(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "catalog_query",
                    "customer_reply": None,
                },
                {
                    "query_kind": "product_list",
                    "filters": {
                        "item_type": {
                            "raw_value": "mug",
                            "constraint_strength": "hard",
                            "catalog_hint": "mug",
                        },
                        "manufacturer": None,
                        "price_constraint": None,
                        "concepts": [
                            {
                                "raw_value": "Ocean",
                                "kind": "theme",
                                "constraint_strength": "hard",
                                "catalog_tag_hints": ["Ocean"],
                            }
                        ],
                        "needs_clarification": False,
                        "clarification_question": None,
                    },
                },
            ]
        )
        result = agent.run_turn("有哪些 Ocean 主题的马克杯？", ConversationState())
        products = result["catalog_data"]["products"]
        self.assertEqual(result["response_type"], "catalog_query")
        self.assertTrue(products)
        self.assertTrue(all(product["item_type"] == "mug" for product in products))
        self.assertTrue(all("Ocean" in product["tags"] for product in products))

    def test_catalog_price_extreme_uses_catalog_fact(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        result = agent.run_turn("最便宜的衬衫是什么？", ConversationState())
        self.assertEqual(result["response_type"], "catalog_query")
        selected = result["catalog_data"]["products"][0]
        self.assertEqual(selected["item_type"], "shirt")
        self.assertIn("最便宜", result["summary"])

    def test_catalog_query_preserves_an_unfinished_recommendation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = TestLLM(agent.repository)
        state = ConversationState()
        first = agent.run_turn("我想买一件T恤", state)
        query = agent.run_turn("你家都有什么价位的衬衫", state)
        follow_up = agent.run_turn("预算低于 20", state)
        self.assertEqual(first["response_type"], "clarification")
        self.assertEqual(query["response_type"], "catalog_query")
        self.assertEqual(query["conversation_state"]["pending_fields"], ["shopping_detail"])
        self.assertEqual(follow_up["response_type"], "recommendation")
        self.assertEqual(agent._reduce_requirement(state).item_type.raw_value, "shirt")

    def test_product_detail_uses_catalog_facts_without_recommendation(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "product_detail",
                    "customer_reply": None,
                }
            ]
        )
        result = agent.run_turn("请介绍 P0005 的描述和标签", ConversationState())
        self.assertEqual(result["response_type"], "product_detail")
        self.assertEqual(result["catalog_data"]["products"][0]["product_id"], "P0005")
        self.assertIsNone(result["purchased_product_id"])

    def test_product_comparison_requires_real_catalog_product_ids(self) -> None:
        agent = ShoppingAgent(DATA_DIR)
        agent.llm = StubLLM(
            [
                {
                    "workflow": "shopping_request",
                    "shopping_task": "product_comparison",
                    "customer_reply": None,
                }
            ]
        )
        result = agent.run_turn("比较 P0005 和 P0006", ConversationState())
        self.assertEqual(result["response_type"], "product_comparison")
        self.assertEqual(
            [product["product_id"] for product in result["catalog_data"]["products"]],
            ["P0005", "P0006"],
        )


if __name__ == "__main__":
    unittest.main()

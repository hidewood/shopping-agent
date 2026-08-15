"""V3 constrained semantic compiler for the shopping conversation.

The model translates language into a small, ordered semantic program.  It never
chooses product IDs, writes conversation state, or performs account actions.
Python compiles that program against one purchase plan, grounds every catalog
fact, executes deterministic queries/recommendations, and returns deferred
effects for the API transaction boundary.
"""

from __future__ import annotations

import hashlib
import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable
from uuid import uuid4

from jsonschema import Draft202012Validator

from starter.llm_client import LLMResponseError


CLAUSE_KINDS = {
    "chat",
    "catalog_query",
    "purchase_set",
    "line_update",
    "line_remove",
    "budget_set",
    "budget_clear",
    "recommend",
    "plan_query",
    "product_query",
    "capability_query",
    "favorite_add",
    "cart_add",
    "order_create",
    "order_cancel",
    "order_query",
    "favorite_list",
    "cart_query",
}
STATEFUL_KINDS = {
    "purchase_set",
    "line_update",
    "line_remove",
    "budget_set",
    "budget_clear",
}
EFFECT_KINDS = {"favorite_add", "cart_add", "order_create", "order_cancel"}
READ_KINDS = {"order_query", "favorite_list", "cart_query"}

_PAYLOAD_CONTRACTS: dict[str, tuple[set[str], set[str]]] = {
    "chat": ({"reply"}, {"reply"}),
    "capability_query": ({"capabilities"}, set()),
    "catalog_query": ({"item_type", "tags", "manufacturer", "operations"}, {"operations"}),
    "product_query": ({"query", "product_ids", "reference"}, {"query"}),
    "purchase_set": ({"mode", "groups", "declared_totals"}, {"mode", "groups"}),
    "line_update": ({"target", "changes", "constraint_mode"}, {"target", "changes"}),
    "line_remove": ({"target"}, {"target"}),
    "budget_set": ({"scope", "target", "min", "max", "currency"}, {"scope"}),
    "budget_clear": ({"scope", "target"}, {"scope"}),
    "recommend": ({"candidate_count", "distinct", "exclude_shown"}, set()),
    "plan_query": ({"query"}, {"query"}),
    "favorite_add": ({"product_ids", "reference", "quantity", "confirmed"}, {"confirmed"}),
    "cart_add": ({"product_ids", "reference", "quantity", "confirmed"}, {"confirmed"}),
    "order_create": ({"product_ids", "reference", "quantity", "confirmed"}, {"confirmed"}),
    "order_cancel": ({"order_id", "confirmed"}, {"confirmed"}),
    "order_query": (set(), set()),
    "favorite_list": (set(), set()),
    "cart_query": (set(), set()),
}


TURN_PROGRAM_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "type": "object",
    "additionalProperties": False,
    "required": ["schema_version", "clauses", "relations"],
    "properties": {
        "schema_version": {"const": "3.0"},
        "primary_act": {"type": ["string", "null"]},
        "clauses": {
            "type": "array",
            "minItems": 1,
            "maxItems": 8,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["id", "kind", "evidence", "payload"],
                "properties": {
                    "id": {"type": "string", "pattern": "^c[1-8]$"},
                    "kind": {"enum": sorted(CLAUSE_KINDS)},
                    "evidence": {"type": "string"},
                    "payload": {"type": "object"},
                },
            },
        },
        "relations": {
            "type": "array",
            "maxItems": 12,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["type", "from", "to"],
                "properties": {
                    "type": {"enum": ["before", "result_reference", "conditional_non_empty", "conditional_empty"]},
                    "from": {"type": "string"},
                    "to": {"type": "string"},
                },
            },
        },
    },
}
_PROGRAM_VALIDATOR = Draft202012Validator(TURN_PROGRAM_SCHEMA)


TURN_PROGRAM_SYSTEM = r"""You are the semantic compiler front-end for a bounded Chinese shopping application.
Return exactly one JSON object. Do not choose products, prices, IDs, database operations, or tool calls.

The root keys are always: schema_version="3.0", primary_act, clauses, relations.
clauses is an ordered array of at most 8 objects with exactly: id, kind, evidence, payload.
evidence must be an exact substring of the latest user message for every state change or account action.
relations may only use before, result_reference, conditional_non_empty, conditional_empty.

Allowed clause kinds and payloads:
- chat: {"reply": concise Chinese reply}. Never make claims about payment, stock, shipping, returns or app capabilities.
- catalog_query: {"item_type": "mug"|"shirt"|null, "tags":[], "manufacturer":null,
  "operations":["count"|"tags"|"manufacturers"|"price_range"|"cheapest"|"list"]}.
- purchase_set: {"mode":"replace"|"merge", "groups":[PurchaseGroup], "declared_totals":[]}.
- line_update: {"target":Target, "changes":{...}, "constraint_mode":"replace"|"merge"|"remove"|null}.
  Include only explicitly changed fields. For constraints, 改成 uses replace, 再加/也喜欢 uses merge,
  and 不要/去掉 uses remove.
- line_remove: {"target":Target}.
- budget_set: {"scope":"plan"|"line"|"unit", "target":Target|null, "min":number|null,
  "max":number|null, "currency":"USD"|"CNY"|null}.
- budget_clear: {"scope":"plan"|"line"|"all", "target":Target|null}.
- recommend: {"candidate_count":integer|null, "distinct":boolean|null, "exclude_shown":boolean|null}.
- plan_query: {"query":"total"|"summary"|"missing"|"recommendations"}.
- product_query: {"query":"detail"|"price"|"compare", "product_ids":[], "reference":Target|null}.
- capability_query: {"capabilities":["payment"|"inventory"|"shipping"|"returns"|"external_info"|"catalog"|"recommendation"|"favorite"|"cart"|"order"]}.
- favorite_add/cart_add/order_create: {"product_ids":[], "reference":Target|null, "quantity":integer|null,
  "confirmed":boolean|null}. An explicit imperative such as 收藏/加入购物车/下单 is confirmed.
- order_cancel: {"order_id":string|null, "confirmed":boolean|null}.
- order_query: {} — 查询当前用户的订单列表（只读）。
- favorite_list: {} — 查询当前用户的收藏列表（只读）。
- cart_query: {} — 查看当前用户的购物车（只读）。

PurchaseGroup exact keys:
{"item_type":"mug"|"shirt"|null,"units":integer,"recipient":string|null,
 "manufacturer":string|null,"constraints":[Constraint],"unit_budget":Price|null,
 "candidate_count":integer|null,"fulfillment_mode":"one_sku"|"distinct_skus"|null}
Constraint exact keys: {"raw_value":string,"strength":"hard"|"preference","catalog_values":[]}
Price exact keys: {"min":number|null,"max":number|null,"currency":"USD"|"CNY"|null}
Target exact keys (only include evidence-based selectors): {"recipient":string|null,"item_type":"mug"|"shirt"|null,
 "ordinal":integer|null,"scope":"one"|"all"|"whole_plan"|null,"raw_reference":string|null}

Important semantics:
- One item and many items use purchase_set groups. Never output internal line IDs.
- A message can have several clauses: “compare A/B then add the cheaper one to cart” needs product_query then cart_add.
- “总价/一共多少钱” is plan_query total, never a budget. “总预算/不超过” is budget_set.
- No stated budget means no budget clause and never asks about budget scope.
- The catalog is priced in USD. A bare number (“预算20以内”、under 20) means USD (currency="USD").
  Set currency="CNY" ONLY when the user explicitly writes ¥ / 元 / 人民币 / RMB / CNY.
- “不限预算/预算取消” is budget_clear while preserving all other constraints.
- “算了/重新来/都不要/改成” with whole-request meaning uses purchase_set mode=replace.
- “喜欢/比较喜欢/最好” means preference; “必须/只要/一定/主题的商品” means hard.
- Unknown styles retain raw_value with catalog_values=[]; never guess a nearby tag.
- A read-only query during a clarification stays a query; it does not answer the pending question.
- When the user answers a pending unknown field, reconstruct the pending purchase_set or line_update
  with that one answer applied; do not treat the short answer as a new unrelated plan.
- Recommendation count is different from purchase units: “推荐三款” sets candidate_count=3 (in the purchase_set group); “买三件” sets units=3.
- “推荐N款X主题的Y”是选购不是目录查询：先给 purchase_set（含 X 主题、candidate_count=N），再给 recommend。
  不要因为缺少“想买/需要”就把商品拆成单独的 catalog_query。
- “再推荐点别的/换一批/换一个/换别的/推荐别的”要不同的商品：recommend 子句带 exclude_shown=true。
- “送人的/送礼/送礼物/当礼物”表示送礼物、未指定收礼人：recipient=null。
  只有“送给X/给X买/买给X”这类明确的人名才设 recipient=X。不要用 someone/somebody 之类的占位名。
- “保证X天到货/能发货吗/多久到货/物流时效”是对物流能力的询问，用 capability_query(shipping)。
  只有明确的“下单/结算/买下/购买这个”指令才用 order_create。
- “支付/付款/结账/pay”是对支付能力的询问或真实交易请求：用 capability_query(payment)，绝不用 order_create/cart_add。
- 带类型 + 预算/主题的表达（如“马克杯预算20以内”“海洋马克杯”），即使没有“想买/推荐/需要”，也默认是选购，
  用 purchase_set（含约束和预算）。budget_set 只在已有采购计划后单独调整预算时用。
- 多类型请求要逐项发出 group：不确定的类型（如“帽子”）也要作为一个 group 发出、保留原文让代码澄清，不要省略任何类型。
- 数量必须是 1-20 的整数；负数、小数、超过 20 的数量要保留原文让代码澄清，不要擅自改成合法数量。
- 主题词即使很长或不常见，也要保留原文放入 constraints（catalog_values 留空），不要省略。
- “我有哪些订单/订单列表/我的订单”用 order_query；“我的收藏/收藏了什么”用 favorite_list；
  “购物车有什么/看看购物车”用 cart_query。它们是只读查询，不改计划、不产生副作用。
- Use relations only for true dependencies. A later action referring to an earlier result uses result_reference.
"""


GROUNDED_SUMMARY_SYSTEM = r"""你是购物助手的回复润色器。根据给定的「事实」写一段自然、亲切、简洁的中文回复。

铁律：
1. 只能使用「事实」里给出的信息——商品名、ID、标签、价格、数量、类型、合计价、放宽条件。
2. 绝不编造目录里没有的商品、价格、库存、物流、支付、退换货；不得添加事实里没有的数字或标签。
3. 「selected」是本次真正推荐的商品，要重点说明并解释为什么推荐它（引用标签、价格与用户约束的匹配）。
   「options」只是其他可选，最多一句话带过（如「另外还有 XX 可选」），不要把它们当成主推荐。
4. 语气像真人导购，1–4 句即可；不要罗列、不要 emoji、不要「作为 AI」「我可以」之类的套话。
5. 无匹配时诚实说明没有满足全部条件；若事实里给了「relaxed」放宽后的最近选择，可以顺带提一句。

返回 JSON，只含一个字段：{"reply": "..."}"""


@dataclass(frozen=True)
class ExecutionContext:
    user_id: str | None = None
    role: str = "guest"
    message_id: str | None = None
    expected_revision: int | None = None


@dataclass
class TurnClause:
    clause_id: str
    kind: str
    evidence: str
    payload: dict[str, Any]


@dataclass
class TurnProgram:
    clauses: list[TurnClause]
    relations: list[dict[str, str]] = field(default_factory=list)
    primary_act: str | None = None

    @classmethod
    def from_dict(cls, raw: Any, message: str) -> "TurnProgram":
        if not isinstance(raw, dict):
            raise LLMResponseError("TurnProgram must be a JSON object.", error_code="invalid_model_output")
        data = dict(raw)
        if "clauses" not in data and isinstance(data.get("acts"), list):
            data["clauses"] = data.pop("acts")
        data.setdefault("schema_version", "3.0")
        data.setdefault("primary_act", None)
        data.setdefault("relations", [])
        for index, clause in enumerate(data.get("clauses") or [], 1):
            if isinstance(clause, dict):
                clause.setdefault("id", f"c{index}")
                if "kind" not in clause and "act" in clause:
                    clause["kind"] = clause.pop("act")
                clause.setdefault("evidence", "")
                clause.setdefault("payload", {})
        error = next(iter(_PROGRAM_VALIDATOR.iter_errors(data)), None)
        if error is not None:
            path = ".".join(str(p) for p in error.absolute_path) or "turn_program"
            raise LLMResponseError(
                f"TurnProgram schema error at {path}: {error.message}",
                error_code="invalid_model_output",
            )
        clauses = [
            TurnClause(str(c["id"]), str(c["kind"]), str(c["evidence"]), dict(c["payload"]))
            for c in data["clauses"]
        ]
        ids = [clause.clause_id for clause in clauses]
        if len(ids) != len(set(ids)):
            raise LLMResponseError("TurnProgram clause IDs must be unique.", error_code="invalid_model_output")
        for clause in clauses:
            allowed, required = _PAYLOAD_CONTRACTS[clause.kind]
            unknown = set(clause.payload) - allowed
            missing = required - set(clause.payload)
            if unknown or missing:
                detail = f"unknown={sorted(unknown)}, missing={sorted(missing)}"
                raise LLMResponseError(
                    f"Clause {clause.clause_id} payload violates its contract: {detail}.",
                    error_code="invalid_model_output",
                )
            if clause.kind in STATEFUL_KINDS | EFFECT_KINDS and (
                not clause.evidence or clause.evidence not in message
            ):
                raise LLMResponseError(
                    f"Clause {clause.clause_id} lacks exact user evidence.",
                    error_code="invalid_model_output",
                )
        for relation in data["relations"]:
            if relation["from"] not in ids or relation["to"] not in ids:
                raise LLMResponseError("TurnProgram relation references an unknown clause.", error_code="invalid_model_output")
            if ids.index(relation["from"]) >= ids.index(relation["to"]):
                raise LLMResponseError(
                    "TurnProgram dependencies must point from an earlier clause to a later clause.",
                    error_code="invalid_model_output",
                )
        return cls(clauses=clauses, relations=list(data["relations"]), primary_act=data.get("primary_act"))


def _default_aggregate() -> dict[str, Any]:
    return {
        "schema_version": 3,
        "active_plan": None,
        "pending_change_set": None,
        "pending_clarification": None,
        "current_result_snapshot": None,
        "last_catalog_context": None,
        "catalog_version": None,
        "capability_policy_version": "1",
        "processed_messages": [],
        "legacy_migrated": False,
    }


def normalize_aggregate(raw: Any) -> dict[str, Any]:
    aggregate = _default_aggregate()
    if isinstance(raw, dict):
        for key in aggregate:
            if key in raw:
                aggregate[key] = deepcopy(raw[key])
    if not isinstance(aggregate["processed_messages"], list):
        aggregate["processed_messages"] = []
    return aggregate


def catalog_version(products: Iterable[dict[str, Any]]) -> str:
    digest = hashlib.sha256()
    for product in sorted(products, key=lambda item: str(item.get("product_id", ""))):
        digest.update(json.dumps(product, ensure_ascii=False, sort_keys=True).encode("utf-8"))
    return digest.hexdigest()[:16]


class SemanticCompilerEngine:
    """Compile a bounded TurnProgram and execute it without model-owned state."""

    CAPABILITIES: dict[str, tuple[bool, str]] = {
        "payment": (False, "目前不支持真实支付。订单仅在本项目中模拟记录。"),
        "inventory": (False, "目录没有实时库存字段，因此不能承诺库存或预留商品。"),
        "shipping": (False, "目前没有接入真实物流；管理员页面中的发货状态仅用于模拟流程。"),
        "returns": (False, "目前没有接入真实退换货服务。"),
        "external_info": (False, "目前没有接入天气、新闻、汇率等实时外部信息。"),
        "catalog": (True, "可以查询当前商品目录中的马克杯和 T 恤。"),
        "recommendation": (True, "可以按商品类型、主题、厂商和美元预算进行推荐。"),
        "favorite": (True, "登录后可以收藏商品，聊天和收藏页面使用同一账户数据。"),
        "cart": (True, "登录后可以把已确认的商品加入购物车。"),
        "order": (True, "登录后可以创建本地模拟订单；不会发起真实支付。"),
    }

    CAPABILITY_INTRO = (
        "我可以帮您挑选商品：查询目录里的马克杯和 T 恤，按类型、主题、厂商和预算进行推荐，"
        "登录后还能收藏商品、加入购物车、创建订单。请问您想找点什么？"
    )

    def __init__(
        self,
        *,
        products: list[dict[str, Any]],
        catalog: dict[str, list[str]],
        item_aliases: dict[str, Iterable[str]],
        tag_aliases: dict[str, Iterable[str]],
        max_candidates: int = 8,
        model_budget_seconds: float = 30.0,
        enable_grounded_summary: bool = True,
    ) -> None:
        self.products = [dict(product) for product in products]
        self.by_id = {str(product["product_id"]).upper(): product for product in self.products}
        self.catalog = {key: list(values) for key, values in catalog.items()}
        self.item_aliases = {key: tuple(values) for key, values in item_aliases.items()}
        self.tag_aliases = {key: tuple(values) for key, values in tag_aliases.items()}
        self.max_candidates = max(1, max_candidates)
        self.model_budget_seconds = min(30.0, max(8.0, model_budget_seconds))
        self.enable_grounded_summary = enable_grounded_summary
        self.catalog_version = catalog_version(self.products)

    def run(
        self,
        message: str,
        aggregate_raw: Any,
        recent_messages: list[dict[str, str]],
        context: ExecutionContext,
        trace: list[dict[str, Any]],
        model_call: Callable[[list[dict[str, str]], str, float], dict[str, Any]],
        store_query: Callable[[str, str | None], dict[str, Any] | None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        aggregate = normalize_aggregate(aggregate_raw)
        aggregate["catalog_version"] = self.catalog_version
        message_id = context.message_id or uuid4().hex
        cached = next(
            (item for item in aggregate["processed_messages"] if item.get("message_id") == message_id),
            None,
        )
        if cached is not None:
            trace.append({"step": "idempotency", "status": "replayed", "message_id": message_id})
            return deepcopy(cached["result"]), aggregate

        program = self._interpret(message, aggregate, recent_messages, trace, model_call)
        result, proposed = self._compile_and_execute(message, program, aggregate, context, trace, store_query)
        if self.enable_grounded_summary:
            result["summary"] = self._generate_grounded_summary(message, result, model_call, trace)
        cached_result = deepcopy(result)
        cached_result["effects"] = []
        proposed["processed_messages"] = (
            list(proposed.get("processed_messages") or [])[-49:]
            + [{"message_id": message_id, "result": cached_result}]
        )
        return result, proposed

    def _interpret(
        self,
        message: str,
        aggregate: dict[str, Any],
        recent_messages: list[dict[str, str]],
        trace: list[dict[str, Any]],
        model_call: Callable[[list[dict[str, str]], str, float], dict[str, Any]],
    ) -> TurnProgram:
        deadline = time.monotonic() + self.model_budget_seconds
        payload = {
            "latest_message": message,
            "recent_messages": recent_messages[-8:],
            "conversation": {
                "active_plan": aggregate.get("active_plan"),
                "pending_change_set": aggregate.get("pending_change_set"),
                "pending_clarification": aggregate.get("pending_clarification"),
                "current_result_snapshot": aggregate.get("current_result_snapshot"),
                "last_catalog_context": aggregate.get("last_catalog_context"),
            },
            "catalog": self.catalog,
            "item_aliases": self.item_aliases,
            "tag_aliases": self.tag_aliases,
            "capabilities": list(self.CAPABILITIES),
        }
        messages = [
            {"role": "system", "content": TURN_PROGRAM_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        raw = model_call(messages, "turn_program", max(1.0, deadline - time.monotonic()))
        try:
            program = TurnProgram.from_dict(raw, message)
        except LLMResponseError as first_error:
            remaining = deadline - time.monotonic()
            if remaining < 2.0:
                raise
            trace.append({"step": "turn_program_validation", "status": "repair_requested"})
            repair_messages = [
                {"role": "system", "content": TURN_PROGRAM_SYSTEM + "\nRepair the invalid object. Preserve user meaning and return JSON only."},
                {"role": "user", "content": json.dumps({"message": message, "invalid": raw, "error": str(first_error)}, ensure_ascii=False)},
            ]
            repaired = model_call(repair_messages, "turn_program_repair", remaining)
            program = TurnProgram.from_dict(repaired, message)
        trace.append({
            "step": "turn_program",
            "status": "validated",
            "clause_kinds": [clause.kind for clause in program.clauses],
        })
        return program

    def _compile_and_execute(
        self,
        message: str,
        program: TurnProgram,
        aggregate: dict[str, Any],
        context: ExecutionContext,
        trace: list[dict[str, Any]],
        store_query: Callable[[str, str | None], dict[str, Any] | None] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        proposed = deepcopy(aggregate)
        summaries: list[str] = []
        visible_products: list[dict[str, Any]] = []
        alternatives: list[dict[str, Any]] = []
        effects: list[dict[str, Any]] = []
        account_data: dict[str, Any] | None = None
        response_type = "chat"
        purchased_product_id: str | None = None
        plan_changed = False
        recommendation_executed_for_generation = -1
        last_clause_products: list[dict[str, Any]] = []
        mutation_seen = False
        mutation_generation = 0
        plan_version_finalized = False
        clause_products: dict[str, list[dict[str, Any]]] = {}

        def record_mutation() -> None:
            nonlocal mutation_seen, mutation_generation, plan_changed
            mutation_seen = True
            mutation_generation += 1
            plan_changed = True
            proposed["current_result_snapshot"] = None

        def finalize_plan_version() -> None:
            nonlocal plan_version_finalized
            if not plan_changed or plan_version_finalized:
                return
            plan = proposed.get("active_plan")
            if plan:
                plan["version"] = int(plan.get("version", 0)) + 1
                plan["status"] = "ready"
            plan_version_finalized = True
            trace.append({
                "step": "purchase_plan_reducer",
                "status": "proposed",
                "line_count": len((plan or {}).get("lines", [])),
            })

        def execute_recommendation(options: dict[str, Any] | None = None) -> list[dict[str, Any]]:
            nonlocal purchased_product_id, response_type
            nonlocal recommendation_executed_for_generation, last_clause_products
            finalize_plan_version()
            distinct = (options or {}).get("distinct")
            exclude_shown = bool((options or {}).get("exclude_shown"))
            recommendation = self._recommend(
                proposed,
                distinct_across_lines=(distinct is not False),
                exclude_shown=exclude_shown,
            )
            summaries.append(recommendation["summary"])
            visible_products.extend(recommendation["products"])
            alternatives.extend(recommendation["alternatives"])
            last_clause_products = recommendation["products"]
            purchased_product_id = recommendation["selected_product_id"]
            response_type = recommendation["response_type"]
            recommendation_executed_for_generation = mutation_generation
            trace.append({
                "step": "deterministic_recommendation",
                "status": "completed" if recommendation["products"] else "no_match",
                "selected_product_ids": [p["product_id"] for p in recommendation["products"]],
            })
            return recommendation["products"]

        for clause in program.clauses:
            kind = clause.kind
            payload = clause.payload
            produced: list[dict[str, Any]] = []
            guards = [relation for relation in program.relations if relation["to"] == clause.clause_id]
            blocked = any(
                (relation["type"] == "conditional_non_empty" and not clause_products.get(relation["from"]))
                or (relation["type"] == "conditional_empty" and bool(clause_products.get(relation["from"])))
                for relation in guards
            )
            if blocked:
                trace.append({"step": "clause_guard", "status": "skipped", "clause_id": clause.clause_id})
                clause_products[clause.clause_id] = []
                continue
            references = [
                relation for relation in guards if relation["type"] == "result_reference"
            ]
            if references:
                last_clause_products = list(clause_products.get(references[-1]["from"], []))
            if kind == "chat":
                summaries.append(self._safe_chat_reply(message, str(payload.get("reply") or "我可以继续帮你挑选商品。")))
            elif kind == "capability_query":
                summaries.append(self._capability_summary(payload.get("capabilities")))
                response_type = "capability"
            elif kind == "catalog_query":
                summary, products = self._catalog_query(payload, proposed)
                summaries.append(summary)
                last_clause_products = products
                visible_products.extend(products)
                produced = products
                response_type = "catalog_query"
            elif kind == "product_query":
                summary, products = self._product_query(payload, proposed)
                summaries.append(summary)
                last_clause_products = products
                visible_products.extend(products)
                produced = products
                response_type = "product_comparison" if len(products) > 1 else "product_detail"
            elif kind == "purchase_set":
                new_plan, problem = self._compile_purchase_set(payload, proposed, clause.evidence)
                if problem:
                    return self._clarification_result(message, problem, proposed, trace)
                proposed["active_plan"] = new_plan
                proposed["pending_change_set"] = None
                proposed["pending_clarification"] = None
                record_mutation()
            elif kind == "line_update":
                problem = self._apply_line_update(proposed, payload, clause.evidence)
                if problem:
                    return self._clarification_result(message, problem, proposed, trace)
                record_mutation()
            elif kind == "line_remove":
                problem = self._apply_line_remove(proposed, payload)
                if problem:
                    return self._clarification_result(message, problem, proposed, trace)
                record_mutation()
                if proposed.get("active_plan") is None:
                    summaries.append("已清空当前采购计划。")
                    response_type = "plan_update"
            elif kind == "budget_set":
                problem = self._apply_budget(proposed, payload, clear=False, evidence=clause.evidence)
                if problem:
                    return self._clarification_result(message, problem, proposed, trace)
                record_mutation()
            elif kind == "budget_clear":
                problem = self._apply_budget(proposed, payload, clear=True, evidence=clause.evidence)
                if problem:
                    return self._clarification_result(message, problem, proposed, trace)
                record_mutation()
            elif kind == "recommend":
                produced = execute_recommendation(payload)
            elif kind == "plan_query":
                finalize_plan_version()
                summary, products = self._plan_query(payload, proposed)
                summaries.append(summary)
                visible_products.extend(products)
                last_clause_products = products
                produced = products
                if response_type not in {"recommendation", "bundle_recommendation", "action"}:
                    response_type = "plan_query"
            elif kind in EFFECT_KINDS:
                finalize_plan_version()
                effect_result = self._compile_effect(
                    kind, payload, proposed, context, last_clause_products, clause.evidence
                )
                if effect_result["summary"]:
                    summaries.append(effect_result["summary"])
                if effect_result["effect"]:
                    effects.append(effect_result["effect"])
                    response_type = "action"
            elif kind in READ_KINDS:
                account_data = store_query(kind, context.user_id) if store_query else None
                if account_data is None:
                    summaries.append("请先登录后再查询账户信息。")
                    response_type = "clarification"
                else:
                    summaries.append(self._format_account(kind, account_data))
                    response_type = "account_query"
            clause_products[clause.clause_id] = produced

        finalize_plan_version()
        if (
            mutation_seen
            and proposed.get("active_plan")
            and recommendation_executed_for_generation != mutation_generation
        ):
            execute_recommendation()

        if not summaries:
            summaries.append("我没有识别到可执行的购物请求，请换一种方式描述。")
            response_type = "clarification"
        products = self._deduplicate_products(visible_products)[: self.max_candidates]
        bundle = self._bundle_payload(proposed) if response_type == "bundle_recommendation" else None
        result = {
            "instruction": message,
            "purchased_product_id": purchased_product_id,
            "summary": "\n".join(part for part in summaries if part),
            "response_type": response_type,
            "catalog_data": {
                "products": products,
                "alternatives": alternatives,
                "snapshot": proposed.get("current_result_snapshot"),
                "bundle": bundle,
            },
            "effects": effects,
            "account_data": account_data,
            "trace": trace,
        }
        return result, proposed

    def _bundle_payload(self, aggregate: dict[str, Any]) -> dict[str, Any] | None:
        plan = aggregate.get("active_plan") or {}
        snapshot = aggregate.get("current_result_snapshot") or {}
        results = {row.get("line_id"): row for row in snapshot.get("line_results", [])}
        items: list[dict[str, Any]] = []
        for line in plan.get("lines", []):
            row = results.get(line.get("line_id")) or {}
            picks = list(row.get("picks") or [])
            product = self.by_id.get(str(picks[0].get("product_id", "")).upper()) if picks else None
            items.append({
                "line_id": line.get("line_id"),
                "recipient": line.get("recipient"),
                "item_type": line.get("item_type"),
                "quantity": line.get("units", 1),
                "product": deepcopy(product) if product else None,
                "picks": deepcopy(picks),
            })
        return {
            "plan_id": plan.get("plan_id"),
            "plan_version": plan.get("version"),
            "items": items,
            "total_price": snapshot.get("deterministic_total"),
        } if items else None

    def _compile_purchase_set(
        self,
        payload: dict[str, Any],
        aggregate: dict[str, Any],
        evidence: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        groups = payload.get("groups")
        if not isinstance(groups, list) or not groups:
            return None, "请告诉我想购买马克杯还是 T 恤。"
        mode = str(payload.get("mode") or "replace")
        current = deepcopy(aggregate.get("active_plan")) if mode == "merge" else None
        plan = current or {
            "plan_id": uuid4().hex[:12],
            "version": 0,
            "status": "collecting",
            "lines": [],
            "shared_budget": None,
            "requested_outputs": [],
            "assumptions": [],
        }
        for raw_group in groups:
            if not isinstance(raw_group, dict):
                return None, "采购条目格式不完整，请重新说明商品和数量。"
            line, problem = self._line_from_group(raw_group, plan, evidence)
            if problem:
                aggregate["pending_change_set"] = {"kind": "purchase_set", "payload": deepcopy(payload)}
                aggregate["pending_clarification"] = self._pending("purchase_line", problem, plan)
                return None, problem
            plan["lines"].append(line)
        declared = payload.get("declared_totals") or []
        for item in declared if isinstance(declared, list) else []:
            if not isinstance(item, dict):
                continue
            item_type = item.get("item_type")
            try:
                expected = int(item.get("units"))
            except (TypeError, ValueError):
                continue
            actual = sum(int(line["units"]) for line in plan["lines"] if line.get("item_type") == item_type)
            if actual != expected:
                return None, f"你提到 {expected} 件 {item_type}，但分配明细合计为 {actual} 件，请确认数量。"
        return plan, None

    def _line_from_group(
        self,
        group: dict[str, Any],
        plan: dict[str, Any],
        evidence: str,
    ) -> tuple[dict[str, Any] | None, str | None]:
        item_type = self._canonical_item_type(group.get("item_type"))
        if item_type is None:
            return None, "请确认这一项是 mug（马克杯）还是 shirt（T 恤）。"
        try:
            units = int(group.get("units", 1))
        except (TypeError, ValueError):
            return None, "商品数量需要是整数。"
        if units < 1 or units > 20:
            return None, "每个采购条目的数量需要在 1–20 之间。"
        constraints, unresolved = self._ground_constraints(group.get("constraints"), evidence)
        if unresolved:
            options = "、".join(self.catalog.get("tags", [])[:10])
            return None, f"目录中无法核验“{unresolved[0]}”这一硬性主题。可以改用 {options} 等已有主题。"
        manufacturer = self._canonical_value(group.get("manufacturer"), self.catalog.get("manufacturers", []))
        if group.get("manufacturer") and manufacturer is None:
            return None, f"目录中没有厂商“{group.get('manufacturer')}”，请换一个目录已有厂商。"
        line_number = 1 + max(
            [int(str(line.get("line_id", "line-0")).split("-")[-1]) for line in plan.get("lines", []) if str(line.get("line_id", "")).startswith("line-")] or [0]
        )
        fulfillment = group.get("fulfillment_mode") or "one_sku"
        if fulfillment not in {"one_sku", "distinct_skus"}:
            fulfillment = "one_sku"
        candidate_count = group.get("candidate_count")
        try:
            candidate_count = int(candidate_count) if candidate_count is not None else 3
        except (TypeError, ValueError):
            candidate_count = 3
        unit_budget = self._price(group.get("unit_budget"), evidence)
        if group.get("unit_budget") is not None and unit_budget is None:
            return None, "单项预算必须是有效的非负价格范围，且下限不能高于上限。"
        if unit_budget and unit_budget.get("currency") == "CNY":
            return None, "当前目录使用美元价格，暂不自动换算人民币；请提供美元预算。"
        return {
            "line_id": f"line-{line_number}",
            "item_type": item_type,
            "units": units,
            "recipient": self._text(group.get("recipient")),
            "manufacturer": manufacturer,
            "constraints": constraints,
            "unit_budget": unit_budget,
            "candidate_count": max(1, min(self.max_candidates, candidate_count)),
            "fulfillment_mode": fulfillment,
        }, None

    def _ground_constraints(
        self,
        raw: Any,
        evidence: str = "",
    ) -> tuple[list[dict[str, Any]], list[str]]:
        result: list[dict[str, Any]] = []
        unresolved: list[str] = []
        for item in raw if isinstance(raw, list) else []:
            if not isinstance(item, dict):
                continue
            raw_value = self._text(item.get("raw_value"))
            if not raw_value:
                continue
            strength = "preference" if item.get("strength") == "preference" else "hard"
            normalized_evidence = evidence.casefold()
            location = normalized_evidence.find(raw_value.casefold())
            if location >= 0:
                window = normalized_evidence[max(0, location - 12): location + len(raw_value) + 12]
                if any(term in window for term in ("必须", "只要", "一定", "不能没有", "must", "required")):
                    strength = "hard"
                elif any(term in window for term in ("喜欢", "偏好", "最好", "优先", "比较喜欢", "prefer", "like")):
                    strength = "preference"
            # Canonical tags are derived from the raw user concept and the
            # repository alias table, never trusted from a model-supplied hint.
            canonical = self._tags_in_text(raw_value)
            name_matches = self._name_matches(raw_value) if not canonical else []
            if not canonical and not name_matches:
                unresolved.append(raw_value)
            result.append({
                "raw_value": raw_value,
                "strength": strength,
                "catalog_values": list(dict.fromkeys(canonical)),
                "name_matches": name_matches,
            })
        return result, unresolved

    def _apply_line_update(
        self,
        aggregate: dict[str, Any],
        payload: dict[str, Any],
        evidence: str,
    ) -> str | None:
        plan = self._working_plan(aggregate)
        if not plan:
            return "当前没有可修改的采购计划，请先说明想购买什么。"
        matches = self._resolve_lines(plan, payload.get("target"))
        if len(matches) != 1:
            return "无法唯一确定你想修改哪一个采购条目，请补充对象或商品类型。"
        line = matches[0]
        changes = payload.get("changes") if isinstance(payload.get("changes"), dict) else {}
        if "units" in changes:
            try:
                units = int(changes["units"])
            except (TypeError, ValueError):
                return "修改后的数量需要是整数。"
            if units < 1 or units > 20:
                return "每个采购条目的数量需要在 1–20 之间。"
            line["units"] = units
        if "recipient" in changes:
            line["recipient"] = self._text(changes.get("recipient"))
        if "item_type" in changes:
            item_type = self._canonical_item_type(changes.get("item_type"))
            if not item_type:
                return "修改后的商品类型必须是马克杯或 T 恤。"
            line["item_type"] = item_type
        if "constraints" in changes:
            constraints, unresolved = self._ground_constraints(changes.get("constraints"), evidence)
            if unresolved:
                aggregate["pending_change_set"] = {"kind": "line_update", "payload": deepcopy(payload)}
                aggregate["pending_clarification"] = self._pending("constraint", unresolved[0], plan)
                return f"目录中无法核验“{unresolved[0]}”这一硬性主题，请换成目录已有主题。"
            mode = payload.get("constraint_mode") or "replace"
            if mode == "merge":
                existing = list(line.get("constraints") or [])
                keys = {
                    (item.get("raw_value"), tuple(item.get("catalog_values") or []), item.get("strength"))
                    for item in existing
                }
                line["constraints"] = existing + [
                    item for item in constraints
                    if (item.get("raw_value"), tuple(item.get("catalog_values") or []), item.get("strength")) not in keys
                ]
            elif mode == "remove":
                remove_raw = {str(item.get("raw_value", "")).casefold() for item in constraints}
                remove_values = {value for item in constraints for value in item.get("catalog_values", [])}
                line["constraints"] = [
                    item for item in line.get("constraints", [])
                    if str(item.get("raw_value", "")).casefold() not in remove_raw
                    and not remove_values.intersection(item.get("catalog_values", []))
                ]
            else:
                line["constraints"] = constraints
        if "manufacturer" in changes:
            manufacturer = self._canonical_value(changes.get("manufacturer"), self.catalog.get("manufacturers", []))
            if changes.get("manufacturer") and not manufacturer:
                return "修改后的厂商不在当前目录中。"
            line["manufacturer"] = manufacturer
        if "unit_budget" in changes:
            budget = self._price(changes.get("unit_budget"), evidence)
            if changes.get("unit_budget") is not None and budget is None:
                return "修改后的预算不是有效价格范围。"
            if budget and budget.get("currency") == "CNY":
                return "当前目录使用美元价格，暂不自动换算人民币。"
            line["unit_budget"] = budget
        aggregate["active_plan"] = plan
        aggregate["pending_change_set"] = None
        aggregate["pending_clarification"] = None
        return None

    def _apply_line_remove(self, aggregate: dict[str, Any], payload: dict[str, Any]) -> str | None:
        plan = self._working_plan(aggregate)
        if not plan:
            return "当前没有可删除的采购条目。"
        matches = self._resolve_lines(plan, payload.get("target"))
        if not matches:
            return "没有找到你想删除的采购条目，请补充对象或商品类型。"
        target = payload.get("target") if isinstance(payload.get("target"), dict) else {}
        if len(matches) > 1 and target.get("scope") != "all":
            return "这句话对应多个采购条目，请说明要删除其中哪一个，或者明确说全部删除。"
        matched_ids = {line["line_id"] for line in matches}
        plan["lines"] = [line for line in plan["lines"] if line["line_id"] not in matched_ids]
        if not plan["lines"]:
            aggregate["active_plan"] = None
            aggregate["current_result_snapshot"] = None
        else:
            aggregate["active_plan"] = plan
        aggregate["pending_clarification"] = None
        return None

    def _apply_budget(self, aggregate: dict[str, Any], payload: dict[str, Any], *, clear: bool, evidence: str = "") -> str | None:
        plan = self._working_plan(aggregate)
        if not plan:
            return "当前没有可设置预算的采购计划，请先说明想购买什么。"
        scope = str(payload.get("scope") or ("plan" if len(plan.get("lines", [])) > 1 else "unit"))
        if clear:
            if scope in {"plan", "all"}:
                plan["shared_budget"] = None
            if scope in {"line", "all"}:
                matches = self._resolve_lines(plan, payload.get("target")) if payload.get("target") else plan["lines"]
                if scope == "line" and len(matches) != 1:
                    return "无法唯一确定要取消哪一个条目的预算。"
                for line in matches:
                    line["unit_budget"] = None
        else:
            price = self._price(payload, evidence)
            if price is None or (price.get("min") is None and price.get("max") is None):
                return "请给出明确的美元预算金额或范围。"
            if price.get("currency") == "CNY":
                return "当前目录价格使用美元，暂不自动换算人民币；请提供美元预算。"
            if scope == "plan":
                plan["shared_budget"] = {**price, "scope": "plan"}
            else:
                matches = self._resolve_lines(plan, payload.get("target")) if payload.get("target") else plan["lines"]
                if scope == "line" and len(matches) != 1:
                    return "无法唯一确定这笔预算对应哪个采购条目。"
                for line in matches:
                    line["unit_budget"] = price
        aggregate["active_plan"] = plan
        aggregate["pending_clarification"] = None
        return None

    def _recommend(
        self,
        aggregate: dict[str, Any],
        *,
        distinct_across_lines: bool = True,
        exclude_shown: bool = False,
    ) -> dict[str, Any]:
        plan = aggregate.get("active_plan")
        if not plan or not plan.get("lines"):
            return {"summary": "请先告诉我想购买马克杯还是 T 恤。", "products": [], "alternatives": [], "selected_product_id": None, "response_type": "clarification"}
        shown_ids: set[str] = set()
        if exclude_shown:
            for line_result in (aggregate.get("current_result_snapshot") or {}).get("line_results", []):
                for pick in line_result.get("picks", []):
                    shown_ids.add(str(pick.get("product_id")).upper())
                for alt in line_result.get("alternatives", []):
                    shown_ids.add(str(alt).upper())
        line_candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]] = []
        alternatives: list[dict[str, Any]] = []
        for line in plan["lines"]:
            products = self._products_for_line(line)
            if exclude_shown and shown_ids:
                filtered = [p for p in products if str(p["product_id"]).upper() not in shown_ids]
                if filtered:
                    products = filtered
            if not products:
                relaxed = self._nearest_for_line(line)
                if relaxed:
                    alternatives.append({"relaxed_constraint": relaxed[0], "products": relaxed[1][:1]})
                plan["status"] = "no_match"
                aggregate["current_result_snapshot"] = None
                return {
                    "summary": f"没有找到满足“{self._line_label(line)}”全部硬条件的商品。可以放宽主题、厂商或预算后再试。",
                    "products": [],
                    "alternatives": alternatives,
                    "selected_product_id": None,
                    "response_type": "no_match",
                }
            if line.get("fulfillment_mode") == "distinct_skus" and len(products) < int(line.get("units", 1)):
                plan["status"] = "no_match"
                aggregate["current_result_snapshot"] = None
                return {
                    "summary": f"“{self._line_label(line)}”要求不同款式，但符合条件的商品数量不足。请减少数量或放宽条件。",
                    "products": [], "alternatives": [], "selected_product_id": None,
                    "response_type": "no_match",
                }
            line_candidates.append((line, products))

        picks = self._choose_plan_products(
            line_candidates,
            plan.get("shared_budget"),
            distinct_across_lines=distinct_across_lines and len(plan["lines"]) > 1,
        )
        if picks is None:
            plan["status"] = "no_match"
            aggregate["current_result_snapshot"] = None
            reason = (
                "各条目都有候选商品，但没有组合能满足当前合计预算。可以提高总预算或放宽部分条件。"
                if plan.get("shared_budget") and plan["shared_budget"].get("max") is not None
                else "各条目都有候选商品，但没有足够的不同商品完成这组推荐。可以允许重复款式或放宽部分条件。"
            )
            return {
                "summary": reason,
                "products": [],
                "alternatives": [],
                "selected_product_id": None,
                "response_type": "no_match",
            }
        snapshot_lines: list[dict[str, Any]] = []
        visible: list[dict[str, Any]] = []
        total = 0.0
        summary_parts: list[str] = []
        for line, candidates, selected in picks:
            units = int(line.get("units", 1))
            if line.get("fulfillment_mode") == "distinct_skus":
                chosen = candidates[: min(units, len(candidates))]
                item_picks = [{"product_id": product["product_id"], "unit_price": float(product["price"]), "units": 1} for product in chosen]
                total += sum(float(product["price"]) for product in chosen)
                visible.extend(chosen)
            else:
                item_picks = [{"product_id": selected["product_id"], "unit_price": float(selected["price"]), "units": units}]
                total += float(selected["price"]) * units
                visible.extend(candidates[: int(line.get("candidate_count", 3))])
            snapshot_lines.append({
                "line_id": line["line_id"],
                "picks": item_picks,
                "alternatives": [product["product_id"] for product in candidates[: self.max_candidates]],
                "applied_constraints": deepcopy(line.get("constraints", [])),
            })
            who = f"给{line['recipient']}的" if line.get("recipient") else ""
            summary_parts.append(f"{who}{line['item_type']}选中 {selected['name']}（{selected['product_id']}）×{units}")
        total = round(total, 2)
        snapshot = {
            "snapshot_id": uuid4().hex[:12],
            "plan_id": plan["plan_id"],
            "plan_version": plan["version"],
            "catalog_version": self.catalog_version,
            "line_results": snapshot_lines,
            "deterministic_total": total,
        }
        aggregate["current_result_snapshot"] = snapshot
        plan["status"] = "recommended"
        summary = "；".join(summary_parts) + f"。合计 ${total:.2f}。"
        return {
            "summary": summary,
            "products": self._deduplicate_products(visible),
            "alternatives": alternatives,
            "selected_product_id": snapshot_lines[0]["picks"][0]["product_id"] if snapshot_lines else None,
            "response_type": "bundle_recommendation" if len(plan["lines"]) > 1 else "recommendation",
        }

    def _choose_plan_products(
        self,
        line_candidates: list[tuple[dict[str, Any], list[dict[str, Any]]]],
        shared_budget: dict[str, Any] | None,
        *,
        distinct_across_lines: bool,
    ) -> list[tuple[dict[str, Any], list[dict[str, Any]], dict[str, Any]]] | None:
        maximum = (
            float(shared_budget["max"])
            if shared_budget and shared_budget.get("max") is not None
            else float("inf")
        )
        # Preserve preference order while bounding the Cartesian search. The
        # lexicographically earliest feasible rank vector wins deterministically.
        pools = [
            candidates[:1] if line.get("fulfillment_mode") == "distinct_skus"
            else candidates[: min(24, len(candidates))]
            for line, candidates in line_candidates
        ]

        def chosen_ids(index: int, primary: dict[str, Any]) -> list[str]:
            line, candidates = line_candidates[index]
            if line.get("fulfillment_mode") == "distinct_skus":
                return [p["product_id"] for p in candidates[: int(line.get("units", 1))]]
            return [primary["product_id"]]

        def option_cost(index: int, primary: dict[str, Any]) -> float:
            line, candidates = line_candidates[index]
            if line.get("fulfillment_mode") == "distinct_skus":
                return sum(float(p["price"]) for p in candidates[: int(line.get("units", 1))])
            return float(primary["price"]) * int(line.get("units", 1))

        # Bounded depth-first search preserves the catalog preference order and
        # supports up to eight lines without materialising a Cartesian product.
        visited = 0

        def search(
            index: int,
            combo: list[dict[str, Any]],
            used: set[str],
            running_total: float,
        ) -> list[dict[str, Any]] | None:
            nonlocal visited
            if index == len(pools):
                return list(combo)
            for product in pools[index]:
                visited += 1
                if visited > 100_000:
                    return None
                ids = set(chosen_ids(index, product))
                if distinct_across_lines and ids.intersection(used):
                    continue
                next_total = running_total + option_cost(index, product)
                if next_total > maximum:
                    continue
                found = search(index + 1, combo + [product], used | ids, next_total)
                if found is not None:
                    return found
            return None

        selected = search(0, [], set(), 0.0)
        if selected is None:
            return None
        return [
            (line_candidates[i][0], line_candidates[i][1], product)
            for i, product in enumerate(selected)
        ]

    def _products_for_line(self, line: dict[str, Any], *, ignore: str | None = None) -> list[dict[str, Any]]:
        hard_groups = [
            constraint.get("catalog_values", [])
            for constraint in line.get("constraints", [])
            if constraint.get("strength") == "hard" and constraint.get("catalog_values")
        ]
        preferred_groups = [
            constraint.get("catalog_values", [])
            for constraint in line.get("constraints", [])
            if constraint.get("strength") == "preference" and constraint.get("catalog_values")
        ]
        hard_name_matches = [
            set(constraint.get("name_matches") or [])
            for constraint in line.get("constraints", [])
            if constraint.get("strength") == "hard" and constraint.get("name_matches")
        ]
        budget = line.get("unit_budget") or {}
        products: list[dict[str, Any]] = []
        for product in self.products:
            if product.get("item_type") != line.get("item_type"):
                continue
            if ignore != "manufacturer" and line.get("manufacturer") and product.get("manufacturer") != line.get("manufacturer"):
                continue
            price = float(product.get("price", 0))
            if ignore != "price":
                if budget.get("min") is not None and price < float(budget["min"]):
                    continue
                if budget.get("max") is not None and price > float(budget["max"]):
                    continue
            tags = set(product.get("tags") or [])
            if ignore != "tags" and any(not tags.intersection(group) for group in hard_groups):
                continue
            if any(product["product_id"] not in ids for ids in hard_name_matches):
                continue
            score = sum(bool(tags.intersection(group)) for group in preferred_groups)
            products.append({**product, "_preference_score": score})
        products.sort(key=lambda product: (-int(product.pop("_preference_score", 0)), float(product["price"]), product["product_id"]))
        return products

    def _nearest_for_line(self, line: dict[str, Any]) -> tuple[str, list[dict[str, Any]]] | None:
        for constraint in ("price", "manufacturer", "tags"):
            products = self._products_for_line(line, ignore=constraint)
            if products:
                return constraint, products
        return None

    def _catalog_query(self, payload: dict[str, Any], aggregate: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        item_type = self._canonical_item_type(payload.get("item_type")) if payload.get("item_type") else None
        tags: list[str] = []
        name_matches: list[str] = []
        for raw in (payload.get("tags") or []):
            raw_text = str(raw)
            matched = self._tags_in_text(raw_text)
            if matched:
                tags.extend(matched)
            else:
                name_matches.extend(self._name_matches(raw_text))
        manufacturer = self._canonical_value(payload.get("manufacturer"), self.catalog.get("manufacturers", []))
        products = [
            product for product in self.products
            if (not item_type or product.get("item_type") == item_type)
            and (not manufacturer or product.get("manufacturer") == manufacturer)
            and (not tags or set(tags).issubset(set(product.get("tags") or [])))
            and (not name_matches or product["product_id"] in set(name_matches))
        ]
        products.sort(key=lambda product: (float(product["price"]), product["product_id"]))
        operations = payload.get("operations") if isinstance(payload.get("operations"), list) else ["count"]
        parts: list[str] = []
        if item_type is None and not tags and not manufacturer and not (set(operations) & {"tags", "manufacturers", "price_range", "cheapest"}):
            # Broad "你们家都卖什么" → a type overview, never an empty result.
            counts: dict[str, int] = {}
            for product in self.products:
                counts[product["item_type"]] = counts.get(product["item_type"], 0) + 1
            label = "、".join(f"{self._type_label(t)} {count} 件" for t, count in sorted(counts.items()))
            parts.append(f"我们出售 {label}")
        else:
            if "count" in operations or "list" in operations:
                parts.append(f"找到 {len(products)} 件符合范围的商品")
            if products and "price_range" in operations:
                parts.append(f"价格 ${products[0]['price']:.2f}–${products[-1]['price']:.2f}")
            if products and "cheapest" in operations:
                parts.append(f"最便宜的是 {products[0]['name']}（{products[0]['product_id']}），${products[0]['price']:.2f}")
            if "tags" in operations:
                counts: dict[str, int] = {}
                for product in products:
                    for tag in product.get("tags") or []:
                        counts[tag] = counts.get(tag, 0) + 1
                top = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))[:12]
                parts.append("常见主题有 " + "、".join(f"{tag}（{count}）" for tag, count in top))
            if "manufacturers" in operations:
                makers = sorted({str(product.get("manufacturer")) for product in products})
                parts.append("厂商包括 " + "、".join(makers[:12]))
        if "list" in operations and products:
            names = "、".join(f"{product['name']}（{product['product_id']}）" for product in products[:6])
            parts.append("例如 " + names + (" 等" if len(products) > 6 else ""))
        aggregate["last_catalog_context"] = {"item_type": item_type, "tags": tags, "manufacturer": manufacturer, "operations": operations}
        visible = products[: self.max_candidates] if "list" in operations or "cheapest" in operations else []
        return ("；".join(parts) + "。") if parts else "没有找到符合条件的商品。", visible

    def _product_query(self, payload: dict[str, Any], aggregate: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        ids = [str(pid).upper() for pid in payload.get("product_ids", []) if str(pid).strip()]
        if not ids and payload.get("reference"):
            ids = self._resolve_product_refs(payload.get("reference"), aggregate, [])
        products = [self.by_id[pid] for pid in ids if pid in self.by_id]
        if not products:
            return "没有找到对应商品，请提供商品 ID，或先让我推荐商品。", []
        if payload.get("query") == "compare" or len(products) > 1:
            details = "；".join(f"{p['name']}（{p['product_id']}）${p['price']:.2f}，主题 {', '.join(p.get('tags') or [])}" for p in products)
            return "对比结果：" + details + "。", products
        product = products[0]
        return f"{product['name']}（{product['product_id']}），${product['price']:.2f}，厂商 {product['manufacturer']}，主题 {', '.join(product.get('tags') or [])}。", products

    def _plan_query(self, payload: dict[str, Any], aggregate: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
        query = payload.get("query")
        plan = aggregate.get("active_plan")
        snapshot = aggregate.get("current_result_snapshot")
        if not plan:
            return "当前还没有采购计划。", []
        if query == "total":
            if not snapshot or snapshot.get("plan_version") != plan.get("version") or snapshot.get("catalog_version") != self.catalog_version:
                return "当前计划还没有有效的推荐结果，请先让我完成推荐后再计算总价。", []
            return f"当前推荐方案合计 ${float(snapshot['deterministic_total']):.2f}。", self._snapshot_products(snapshot)
        if query == "recommendations":
            return "当前推荐如下。", self._snapshot_products(snapshot) if snapshot else []
        if query == "missing":
            missing = [self._line_label(line) for line in plan.get("lines", []) if not line.get("item_type")]
            return ("仍需确认：" + "、".join(missing) + "。") if missing else "当前计划没有阻塞执行的缺失字段。", []
        lines = "；".join(f"{self._line_label(line)} ×{line.get('units', 1)}" for line in plan.get("lines", []))
        return f"当前采购计划：{lines}。", self._snapshot_products(snapshot) if snapshot else []

    def _compile_effect(
        self,
        kind: str,
        payload: dict[str, Any],
        aggregate: dict[str, Any],
        context: ExecutionContext,
        recent_products: list[dict[str, Any]],
        evidence: str,
    ) -> dict[str, Any]:
        # 支付/付款不是可执行的账户动作，直接诚实拒绝（无论是否登录）
        if any(term in (evidence or "").casefold() for term in ("支付", "付款", "结账", "pay", "payment", "checkout")):
            return {"summary": "目前不支持真实支付。订单仅在本项目中模拟记录。", "effect": None}
        if context.user_id is None:
            return {"summary": "请先登录后再使用收藏、购物车或模拟订单功能。", "effect": None}
        if payload.get("confirmed") is not True:
            return {"summary": "这项操作会修改你的账户数据，请明确确认后再执行。", "effect": None}
        action_terms = {
            "favorite_add": ("收藏", "加入收藏", "save", "bookmark"),
            "cart_add": ("购物车", "加购", "add to cart"),
            "order_create": ("下单", "创建订单", "买下", "结算", "购买这个", "购买它", "place order"),
            "order_cancel": ("取消订单", "撤销订单", "cancel order"),
        }
        normalized_evidence = evidence.casefold()
        if not any(term in normalized_evidence for term in action_terms.get(kind, ())):
            return {"summary": "我没有在你的原话中找到明确的账户操作指令，因此没有执行。", "effect": None}
        if kind == "order_cancel":
            order_id = self._text(payload.get("order_id"))
            if not order_id:
                return {"summary": "请提供要取消的模拟订单编号。", "effect": None}
            return {"summary": f"已提交取消模拟订单 {order_id} 的请求。", "effect": {"kind": "order.cancel", "order_id": order_id}}
        ids = [str(pid).upper() for pid in payload.get("product_ids", []) if str(pid).upper() in self.by_id]
        if not ids:
            ids = self._resolve_product_refs(payload.get("reference"), aggregate, recent_products)
        if not ids and len(recent_products) == 1:
            ids = [recent_products[0]["product_id"]]
        if not ids and kind in {"order_create", "cart_add"}:
            # Bare "下单/加购物车" after a recommendation means the whole current plan.
            snapshot_ids = [
                pick["product_id"]
                for line in (aggregate.get("current_result_snapshot") or {}).get("line_results", [])
                for pick in line.get("picks", [])
            ]
            ids = list(dict.fromkeys(snapshot_ids))
        if not ids:
            return {"summary": "无法唯一确定要操作的商品，请提供商品 ID 或明确说第几个。", "effect": None}
        try:
            quantity = max(1, min(99, int(payload.get("quantity") or 1)))
        except (TypeError, ValueError):
            quantity = 1
        if kind == "favorite_add":
            return {"summary": "已收藏 " + "、".join(ids) + "。", "effect": {"kind": "favorite.add", "product_ids": ids}}
        if kind == "cart_add":
            return {"summary": "已加入购物车：" + "、".join(ids) + "。", "effect": {"kind": "cart.add", "items": [{"product_id": pid, "quantity": quantity} for pid in ids]}}
        items = []
        snapshot = aggregate.get("current_result_snapshot") or {}
        if not payload.get("product_ids") and snapshot:
            for line in snapshot.get("line_results", []):
                for pick in line.get("picks", []):
                    if pick.get("product_id") in ids:
                        items.append({"product_id": pick["product_id"], "quantity": int(pick.get("units", 1)), "price": float(pick.get("unit_price", 0))})
        if not items:
            items = [{"product_id": pid, "quantity": quantity, "price": float(self.by_id[pid]["price"])} for pid in ids]
        order_id = "ORD-" + hashlib.sha256(f"{context.message_id}:{context.user_id}".encode()).hexdigest()[:8].upper()
        return {
            "summary": f"已创建本地模拟订单 {order_id}；不会发起真实支付。",
            "effect": {"kind": "order.create", "order_id": order_id, "items": items},
        }

    def _resolve_lines(self, plan: dict[str, Any], raw_target: Any) -> list[dict[str, Any]]:
        target = raw_target if isinstance(raw_target, dict) else {}
        lines = list(plan.get("lines") or [])
        if target.get("scope") == "whole_plan":
            return lines
        recipient = self._text(target.get("recipient"))
        item_type = self._canonical_item_type(target.get("item_type")) if target.get("item_type") else None
        matches = [line for line in lines if (not recipient or self._same_reference(recipient, line.get("recipient"))) and (not item_type or line.get("item_type") == item_type)]
        ordinal = target.get("ordinal")
        if ordinal is not None:
            try:
                index = int(ordinal) - 1
                return [lines[index]] if 0 <= index < len(lines) else []
            except (TypeError, ValueError):
                return []
        return matches if (recipient or item_type or target.get("scope") in {"all", "whole_plan"}) else (lines if len(lines) == 1 else [])

    def _resolve_product_refs(self, raw_ref: Any, aggregate: dict[str, Any], recent_products: list[dict[str, Any]]) -> list[str]:
        ref = raw_ref if isinstance(raw_ref, dict) else {}
        snapshot = aggregate.get("current_result_snapshot")
        products = recent_products or self._snapshot_products(snapshot)
        if not products:
            return []
        ordinal = ref.get("ordinal")
        if ordinal is not None:
            try:
                index = int(ordinal) - 1
                return [products[index]["product_id"]] if 0 <= index < len(products) else []
            except (TypeError, ValueError):
                return []
        raw = str(ref.get("raw_reference") or "").casefold()
        snapshot_lines = list((snapshot or {}).get("line_results") or [])
        if (
            len(snapshot_lines) == 1
            and (not raw or any(word in raw for word in ("它", "这个", "选中", "推荐的", "that", "it")))
        ):
            picks = list(snapshot_lines[0].get("picks") or [])
            if len(picks) == 1 and picks[0].get("product_id") in self.by_id:
                return [picks[0]["product_id"]]
        if snapshot_lines and any(word in raw for word in ("整套", "这套", "方案", "选中的", "推荐结果", "the plan")):
            return list(dict.fromkeys(
                str(pick["product_id"])
                for line in snapshot_lines
                for pick in line.get("picks", [])
                if pick.get("product_id") in self.by_id
            ))
        if any(word in raw for word in ("便宜", "cheapest", "最低")):
            return [min(products, key=lambda product: (float(product["price"]), product["product_id"]))["product_id"]]
        if ref.get("scope") == "all" or any(word in raw for word in ("这些", "全部", "整套", "all")):
            return [product["product_id"] for product in products]
        return [products[0]["product_id"]] if len(products) == 1 else []

    def _capability_summary(self, raw: Any) -> str:
        names = [name for name in raw if name in self.CAPABILITIES] if isinstance(raw, list) else []
        if not names or len(names) >= 5:
            # A general "你能做什么" question — lead with what the agent CAN do,
            # not a laundry list of unsupported features.
            return self.CAPABILITY_INTRO
        supported = [name for name in names if self.CAPABILITIES[name][0]]
        unsupported = [name for name in names if not self.CAPABILITIES[name][0]]
        if unsupported and not supported:
            # A specific "能付款吗 / 能发货吗" question — answer that one honestly.
            return "".join(self.CAPABILITIES[name][1] for name in unsupported)
        return "".join(self.CAPABILITIES[name][1] for name in names)

    @staticmethod
    def _format_account(kind: str, data: dict[str, Any]) -> str:
        """Deterministic summary of the logged-in user's orders / favorites / cart."""
        if kind == "order_query":
            orders = data.get("orders") or []
            if not orders:
                return "您目前还没有订单。"
            parts = []
            for o in orders:
                items = "、".join(
                    f"{i.get('product', {}).get('name', i.get('product_id'))}×{i.get('quantity', 1)}"
                    for i in o.get("items", [])
                )
                parts.append(f"{o['order_id']}（{o['status']}，{items}，共 ${o['total_price']:.2f}）")
            return f"您目前有 {len(orders)} 个订单：" + "；".join(parts) + "。"
        if kind == "favorite_list":
            favorites = data.get("favorites") or []
            if not favorites:
                return "您还没有收藏任何商品。"
            names = "、".join(f.get("product", {}).get("name", f.get("product_id")) for f in favorites)
            return f"您收藏了 {len(favorites)} 件商品：" + names + "。"
        if kind == "cart_query":
            cart = data.get("cart") or {}
            items = cart.get("items") or []
            if not items:
                return "您的购物车是空的。"
            names = "、".join(
                f"{i.get('product', {}).get('name', i.get('product_id'))}×{i.get('quantity', 1)}"
                for i in items
            )
            total = cart.get("total_price", 0)
            return f"您的购物车有 {len(items)} 种商品：" + names + f"，合计 ${total:.2f}。"
        return "已查询。"

    def _safe_chat_reply(self, message: str, reply: str) -> str:
        lower = message.casefold()
        matched = [name for name, terms in {
            "payment": ("支付", "付款", "payment", "pay"),
            "inventory": ("库存", "现货", "inventory", "stock"),
            "shipping": ("物流", "配送", "发货", "shipping", "delivery"),
            "returns": ("退货", "退款", "return", "refund"),
            "external_info": ("天气", "气温", "新闻", "汇率", "weather", "news", "exchange rate"),
        }.items() if any(term in lower for term in terms)]
        return self._capability_summary(matched) if matched else reply[:240]

    def _clarification_result(
        self,
        message: str,
        question: str,
        aggregate: dict[str, Any],
        trace: list[dict[str, Any]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        aggregate["pending_clarification"] = aggregate.get("pending_clarification") or self._pending("semantic", question, aggregate.get("active_plan"))
        trace.append({"step": "semantic_compiler", "status": "clarification_required"})
        return {
            "instruction": message,
            "purchased_product_id": None,
            "summary": question,
            "response_type": "clarification",
            "catalog_data": {"products": [], "alternatives": []},
            "effects": [],
            "trace": trace,
        }, aggregate

    @staticmethod
    def _pending(kind: str, question: str, plan: dict[str, Any] | None) -> dict[str, Any]:
        return {
            "clarification_id": uuid4().hex[:12],
            "kind": kind,
            "question": question,
            "base_plan_id": (plan or {}).get("plan_id"),
            "base_plan_version": (plan or {}).get("version"),
            "interruption_policy": "preserve_on_read_only",
        }

    @staticmethod
    def _working_plan(aggregate: dict[str, Any]) -> dict[str, Any] | None:
        pending = aggregate.get("pending_change_set")
        if isinstance(pending, dict) and isinstance(pending.get("proposed_plan"), dict):
            return deepcopy(pending["proposed_plan"])
        plan = aggregate.get("active_plan")
        return deepcopy(plan) if isinstance(plan, dict) else None

    def _canonical_item_type(self, raw: Any) -> str | None:
        value = self._text(raw)
        if not value:
            return None
        normalized = value.casefold()
        for canonical in self.catalog.get("item_types", []):
            aliases = (canonical, *self.item_aliases.get(canonical, ()))
            if any(alias.casefold() == normalized or alias.casefold() in normalized for alias in aliases):
                return canonical
        return None

    def _type_label(self, item_type: str) -> str:
        aliases = self.item_aliases.get(item_type, ())
        return f"{aliases[0] if aliases else item_type}（{item_type}）"

    @staticmethod
    def _canonical_value(raw: Any, allowed: Iterable[str]) -> str | None:
        if raw is None:
            return None
        normalized = str(raw).strip().casefold()
        return next((value for value in allowed if value.casefold() == normalized), None)

    def _tags_in_text(self, text: str) -> list[str]:
        lower = text.casefold()
        matched: list[str] = []
        for canonical in self.catalog.get("tags", []):
            canon = canonical.casefold()
            if canon in lower:
                matched.append(canonical)
            elif any(alias.casefold() in lower for alias in self.tag_aliases.get(canonical, ())):
                matched.append(canonical)
            else:
                stem = self._stem(canon)
                if len(stem) >= 5 and stem in lower:
                    matched.append(canonical)
        return list(dict.fromkeys(matched))

    @staticmethod
    def _stem(word: str) -> str:
        """Naive singular stem, so "strawberry" matches the tag "strawberries"."""
        w = word.casefold()
        if w.endswith("ies") and len(w) > 4:
            return w[:-3] + "y"
        if w.endswith("es") and len(w) > 3:
            return w[:-2]
        if w.endswith("s") and len(w) > 2:
            return w[:-1]
        return w

    @staticmethod
    def _searchable_text(product: dict[str, Any]) -> str:
        parts = [product.get("name", ""), product.get("description", ""), *(product.get("tags") or [])]
        return " ".join(str(p) for p in parts if p).casefold()

    def _name_matches(self, raw_value: str) -> list[str]:
        """Deterministic name/description fallback for concepts absent from the tag
        vocabulary. Returns product IDs whose searchable text contains the concept,
        with light plural tolerance (strawberry -> strawberries)."""
        key = raw_value.casefold().strip()
        if len(key) < 4:
            return []
        forms = {key, key + "s", key + "es"}
        if key.endswith("y"):
            forms.add(key[:-1] + "ies")
        return [
            str(product["product_id"])
            for product in self.products
            if any(form in self._searchable_text(product) for form in forms)
        ]

    def _price(self, raw: Any, evidence: str = "") -> dict[str, Any] | None:
        if not isinstance(raw, dict):
            return None
        def number(value: Any) -> float | None:
            try:
                return float(value) if value is not None else None
            except (TypeError, ValueError):
                return None
        minimum, maximum = number(raw.get("min")), number(raw.get("max"))
        if (minimum is not None and minimum < 0) or (maximum is not None and maximum < 0):
            return None
        if minimum is not None and maximum is not None and minimum > maximum:
            return None
        return {"min": minimum, "max": maximum, "currency": self._ground_currency(evidence)}

    def _ground_currency(self, evidence: str) -> str:
        """Ground the budget currency from the user's exact words, never the model's guess."""
        text = evidence or ""
        if re.search(r"人民币|RMB|CNY|¥", text, re.IGNORECASE):
            return "CNY"
        if "元" in text and "美元" not in text:
            return "CNY"
        return "USD"

    @staticmethod
    def _text(raw: Any) -> str | None:
        value = str(raw).strip() if raw is not None else ""
        return value or None

    @staticmethod
    def _same_reference(first: str, second: Any) -> bool:
        a = re.sub(r"\s+", "", first.casefold()).replace("我自己", "我").replace("自己", "我")
        b = re.sub(r"\s+", "", str(second or "").casefold()).replace("我自己", "我").replace("自己", "我")
        return bool(a and b and (a == b or a in b or b in a))

    @staticmethod
    def _line_label(line: dict[str, Any]) -> str:
        recipient = f"给{line['recipient']}的" if line.get("recipient") else ""
        tags = [value for constraint in line.get("constraints", []) for value in constraint.get("catalog_values", [])]
        theme = f"{'/'.join(tags)} 主题" if tags else ""
        return f"{recipient}{theme}{line.get('item_type', '商品')}"

    def _snapshot_products(self, snapshot: Any) -> list[dict[str, Any]]:
        if not isinstance(snapshot, dict) or snapshot.get("catalog_version") != self.catalog_version:
            return []
        ids = [pick.get("product_id") for line in snapshot.get("line_results", []) for pick in line.get("picks", [])]
        return [self.by_id[pid] for pid in ids if pid in self.by_id]

    def _summary_facts(self, result: dict[str, Any]) -> dict[str, Any]:
        """Extract grounded facts, separating the SELECTED products from other options."""
        catalog = result.get("catalog_data") or {}

        def _slim(product: dict[str, Any], quantity: int = 1) -> dict[str, Any]:
            return {
                "name": product.get("name"),
                "price": product.get("price"),
                "tags": list(product.get("tags") or []),
                "item_type": product.get("item_type"),
                "quantity": quantity,
            }

        selected: list[dict[str, Any]] = []
        selected_ids: set[str] = set()
        bundle = catalog.get("bundle") or {}
        for item in bundle.get("items", []):
            product = item.get("product")
            if product:
                selected.append(_slim(product, item.get("quantity", 1)))
                selected_ids.add(str(product.get("product_id")).upper())
        if not selected:
            pid = result.get("purchased_product_id")
            if pid:
                product = self.by_id.get(str(pid).upper())
                if product:
                    selected.append(_slim(product))
                    selected_ids.add(str(pid).upper())

        options: list[dict[str, Any]] = []
        for product in (catalog.get("products") or []):
            if str(product.get("product_id")).upper() not in selected_ids:
                options.append(_slim(product))

        facts: dict[str, Any] = {
            "response_type": result.get("response_type"),
            "selected": selected,
            "options": options[:6],
        }
        snapshot = catalog.get("snapshot") or {}
        if snapshot.get("deterministic_total") is not None:
            facts["total_price"] = snapshot["deterministic_total"]
        if bundle.get("total_price") is not None:
            facts["total_price"] = bundle["total_price"]
        relaxed = catalog.get("alternatives") or []
        if relaxed:
            facts["relaxed"] = [
                {
                    "relaxed_constraint": alt.get("relaxed_constraint"),
                    "products": [p.get("name") for p in (alt.get("products") or [])],
                }
                for alt in relaxed
            ]
        return facts

    def _generate_grounded_summary(
        self,
        message: str,
        result: dict[str, Any],
        model_call: Callable[[list[dict[str, str]], str, float], dict[str, Any]],
        trace: list[dict[str, Any]],
    ) -> str:
        """Rewrite the templated summary as natural language, grounded in Python facts.

        Falls back to the deterministic summary on any model failure, so the
        grounding guarantee (facts come from Python) is never weakened by a
        generation outage.
        """
        response_type = result.get("response_type")
        if response_type in {"chat", "capability", "service_error", "action"}:
            return result.get("summary", "")
        facts = self._summary_facts(result)
        if not facts.get("selected") and not facts.get("options") and response_type not in {"clarification", "no_match", "plan_query", "catalog_query"}:
            return result.get("summary", "")
        payload = {
            "user_message": message,
            "facts": facts,
            "templated_summary": result.get("summary", ""),
        }
        messages = [
            {"role": "system", "content": GROUNDED_SUMMARY_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
        ]
        try:
            raw = model_call(messages, "grounded_summary", 8.0)
        except Exception:
            # Best-effort enhancement: never let a generation outage weaken the
            # grounding guarantee or break the turn. Fall back to the template.
            trace.append({"step": "grounded_summary", "status": "fallback"})
            return result.get("summary", "")
        reply = raw.get("reply") if isinstance(raw, dict) else None
        if reply and str(reply).strip():
            trace.append({"step": "grounded_summary", "status": "generated"})
            return str(reply).strip()
        trace.append({"step": "grounded_summary", "status": "fallback"})
        return result.get("summary", "")

    @staticmethod
    def _deduplicate_products(products: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        seen: set[str] = set()
        for product in products:
            product_id = str(product.get("product_id", ""))
            if product_id and product_id not in seen:
                clean = {key: value for key, value in product.items() if not key.startswith("_")}
                result.append(clean)
                seen.add(product_id)
        return result

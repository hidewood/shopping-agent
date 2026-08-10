from __future__ import annotations

"""Single-file implementation of the shopping Agent task interface.

The workflow is intentionally kept here so `Agent(data_dir).run(instruction)` is
self-contained: configuration, structured prompts, catalog grounding, deterministic
constraint checks, and deterministic candidate ranking all live in this file.
"""

# ===== config.py =====

import os
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from dotenv import load_dotenv


PROJECT_DIR = Path(__file__).resolve().parents[1]
load_dotenv(PROJECT_DIR / ".env")


class ConfigurationError(RuntimeError):
    """Raised when a required local configuration value is missing."""


@dataclass(frozen=True)
class Settings:
    api_key: str
    model: str
    max_candidates: int
    timeout_seconds: float
    max_retries: int

    @classmethod
    def from_environment(cls) -> "Settings":
        api_key = os.getenv("DEEPSEEK_API_KEY", "").strip()
        if not api_key:
            raise ConfigurationError(
                "DEEPSEEK_API_KEY is missing. Copy .env.example to .env and set the key."
            )

        raw_limit = os.getenv("AGENT_MAX_CANDIDATES", "8")
        try:
            max_candidates = max(1, int(raw_limit))
        except ValueError as exc:
            raise ConfigurationError("AGENT_MAX_CANDIDATES must be an integer.") from exc

        try:
            timeout_seconds = max(1.0, float(os.getenv("DEEPSEEK_TIMEOUT_SECONDS", "45")))
        except ValueError as exc:
            raise ConfigurationError("DEEPSEEK_TIMEOUT_SECONDS must be a number.") from exc
        try:
            max_retries = max(0, min(2, int(os.getenv("DEEPSEEK_MAX_RETRIES", "1"))))
        except ValueError as exc:
            raise ConfigurationError("DEEPSEEK_MAX_RETRIES must be an integer between 0 and 2.") from exc

        return cls(
            api_key=api_key,
            model=os.getenv("DEEPSEEK_MODEL", "deepseek-v4-pro").strip(),
            max_candidates=max_candidates,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )


# ===== schemas.py =====

from dataclasses import asdict, dataclass, field
from typing import Any


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _strength(value: Any, default: str = "hard") -> str:
    text = str(value).strip().lower()
    return text if text in {"hard", "preference"} else default


@dataclass
class CatalogConstraint:
    """A user expression and an optional model-suggested catalog value."""

    raw_value: str | None = None
    constraint_strength: str = "hard"
    catalog_hint: str | None = None

    @classmethod
    def from_value(cls, value: Any, default_strength: str = "hard") -> "CatalogConstraint":
        if isinstance(value, dict):
            return cls(
                raw_value=_optional_text(value.get("raw_value")),
                constraint_strength=_strength(value.get("constraint_strength"), default_strength),
                catalog_hint=_optional_text(value.get("catalog_hint")),
            )
        return cls(raw_value=_optional_text(value), constraint_strength=default_strength)


@dataclass
class Concept:
    raw_value: str
    kind: str = "other"
    constraint_strength: str = "hard"
    catalog_tag_hints: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any, default_strength: str = "hard") -> "Concept | None":
        if isinstance(data, str):
            raw_value = _optional_text(data)
            return cls(raw_value=raw_value, constraint_strength=default_strength) if raw_value else None
        if not isinstance(data, dict):
            return None
        raw_value = _optional_text(data.get("raw_value"))
        if not raw_value:
            return None
        return cls(
            raw_value=raw_value,
            kind=_optional_text(data.get("kind")) or "other",
            constraint_strength=_strength(data.get("constraint_strength"), default_strength),
            catalog_tag_hints=_string_list(data.get("catalog_tag_hints")),
        )


@dataclass
class PriceConstraint:
    """A single-sided price comparison or an inclusive/exclusive price range.

    ``operator`` / ``value`` remain accepted for older planner outputs and test
    fixtures.  New range-aware outputs use ``min_value`` and ``max_value`` so a
    follow-up such as “10 元以上、20 元以下” never has to discard one bound.
    """

    operator: str | None = None
    value: float | None = None
    min_value: float | None = None
    max_value: float | None = None
    min_inclusive: bool = True
    max_inclusive: bool = True

    @classmethod
    def from_value(cls, value: Any) -> "PriceConstraint":
        if isinstance(value, dict):
            has_legacy_value = value.get("operator") is not None or value.get("value") is not None
            has_range_keys = (
                value.get("min_value") is not None
                or value.get("max_value") is not None
                or (
                    not has_legacy_value
                    and any(
                        key in value
                        for key in ("min_value", "max_value", "min_inclusive", "max_inclusive")
                    )
                )
            )
            if has_range_keys:
                min_value = cls._number(value.get("min_value"))
                max_value = cls._number(value.get("max_value"))
                if min_value is not None and max_value is not None and min_value > max_value:
                    raise LLMResponseError(
                        "price_constraint min_value cannot exceed max_value.",
                        error_code="invalid_model_output",
                    )
                return cls(
                    min_value=min_value,
                    max_value=max_value,
                    min_inclusive=cls._boolean(value.get("min_inclusive"), True),
                    max_inclusive=cls._boolean(value.get("max_inclusive"), True),
                )
            operator = _optional_text(value.get("operator"))
            if operator not in {"<", "<=", "=", ">=", ">"}:
                operator = None
            raw_number = value.get("value")
        else:
            # Backward-compatible interpretation of the original max_price field.
            operator = "<="
            raw_number = value
        number = cls._number(raw_number)
        return cls(operator=operator if number is not None else None, value=number)

    @staticmethod
    def _number(value: Any) -> float | None:
        try:
            return float(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _boolean(value: Any, default: bool) -> bool:
        return value if isinstance(value, bool) else default

    def has_value(self) -> bool:
        return bool(
            self.value is not None
            or self.min_value is not None
            or self.max_value is not None
        )

    def to_dict(self) -> dict[str, Any]:
        """Return the format expected by the planner, retaining legacy single bounds."""
        if self.operator is not None and self.value is not None:
            return {"operator": self.operator, "value": self.value}
        return {
            "min_value": self.min_value,
            "max_value": self.max_value,
            "min_inclusive": self.min_inclusive,
            "max_inclusive": self.max_inclusive,
        }


@dataclass
class ShoppingRequirement:
    """Semantic request returned by the model before catalog grounding."""

    item_type: CatalogConstraint = field(default_factory=CatalogConstraint)
    manufacturer: CatalogConstraint = field(default_factory=CatalogConstraint)
    price_constraint: PriceConstraint = field(default_factory=PriceConstraint)
    concepts: list[Concept] = field(default_factory=list)
    needs_clarification: bool = False
    clarification_question: str | None = None

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ShoppingRequirement":
        if not isinstance(data, dict):
            raise LLMResponseError("Shopping requirement must be a JSON object.")
        raw_concepts = data.get("concepts", [])
        if raw_concepts is None:
            raw_concepts = []
        if not isinstance(raw_concepts, list):
            raise LLMResponseError("Shopping requirement concepts must be an array or null.")
        concepts = [Concept.from_dict(item) for item in raw_concepts]
        # Safely accept the original schema if an older response format is encountered.
        if not concepts:
            concepts = [
                Concept.from_dict(item, "hard") for item in data.get("required_keywords", [])
            ] + [
                Concept.from_dict(item, "preference")
                for item in data.get("preferred_keywords", [])
            ]

        raw_needs_clarification = data.get("needs_clarification", False)
        if not isinstance(raw_needs_clarification, bool):
            raise LLMResponseError("needs_clarification must be a boolean.")

        return cls(
            item_type=CatalogConstraint.from_value(data.get("item_type"), "hard"),
            manufacturer=CatalogConstraint.from_value(data.get("manufacturer"), "hard"),
            price_constraint=PriceConstraint.from_value(
                data.get("price_constraint", data.get("max_price"))
            ),
            concepts=[concept for concept in concepts if concept is not None],
            needs_clarification=raw_needs_clarification,
            clarification_question=_optional_text(data.get("clarification_question")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


CATALOG_OPERATIONS = {
    "count",
    "group_by_item_type",
    "group_by_manufacturer",
    "group_by_tag",
    "list",
    "price_range",
    "price_extreme",
}

# Turns where the user lacks catalog information and proactive guidance helps.
# A recommendation drawn from a small candidate pool is excluded: the answer is
# already precise, so appending suggestions competes with it.  A recommendation
# drawn from hundreds of candidates is included, because the single product shown
# tells the user almost nothing about what else the catalog holds.
PROACTIVE_RESPONSE_TYPES = {"catalog_query", "clarification", "exploration", "no_match", "recommendation"}

# At or below this many matches the result set is small enough to speak for
# itself: show every product instead of teaching the user how to narrow further.
FEW_RESULTS_THRESHOLD = 5

PLAN_GOALS = {"chat", "information", "selection", "action"}
PLAN_TARGETS = {"none", "catalog", "product", "transaction"}
TRANSACTION_ACTIONS = {"order.create", "order.cancel", "payment.create"}
CAPABILITY_REGISTRY = {
    "catalog.read": "supported",
    "recommendation.generate": "supported",
    "order.create": "unsupported",
    "order.cancel": "unsupported",
    "payment.create": "unsupported",
}


@dataclass(frozen=True)
class RecommendationPolicy:
    """Product policy, not an LLM judgement, for when a selection may be made.

    In this catalog retrieval is almost always a better answer than a question.
    The item type is the one genuinely blocking gap: `mug` and `shirt` are
    disjoint sets, so without it there is nothing meaningful to retrieve.  Every
    other missing condition is better resolved by showing real products and the
    verified ways to narrow them, because asking about a field the catalog does
    not record ("what material?") cannot change any filter.
    """

    def is_ready(self, requirement: ShoppingRequirement) -> bool:
        return bool(requirement.item_type.raw_value)


@dataclass(frozen=True)
class DialoguePolicy:
    """Choose the next conversational stage from verified, replayed state.

    This policy does not parse language or choose products.  It makes the
    multi-turn experience explicit: after a user first names a product type, the
    agent introduces the real catalog and invites a free-form refinement instead
    of pretending the user has already supplied a complete shopping brief.
    """

    def selection_stage(
        self, requirement: ShoppingRequirement, selection_mode: str | None
    ) -> str:
        if not requirement.item_type.raw_value:
            return "collecting"
        has_refinement = bool(
            requirement.manufacturer.raw_value
            or requirement.price_constraint.has_value()
            or requirement.concepts
        )
        if not has_refinement and selection_mode != "explicitly_open":
            return "exploring"
        return "recommending"


@dataclass
class TurnPlan:
    """The sole model-produced plan for one visible customer turn.

    The plan is intentionally declarative: it names a bounded intent and catalog
    operations, while Python remains responsible for data access, state mutation,
    and all catalog facts.
    """

    goal: str
    target: str
    customer_reply: str | None = None
    requirement: ShoppingRequirement | None = None
    catalog_operations: list[str] = field(default_factory=list)
    state_action: str = "none"
    selection_mode: str | None = None
    action: str | None = None
    goal_evidence: list[str] = field(default_factory=list)

    @classmethod
    def from_dict(cls, data: Any) -> "TurnPlan":
        if not isinstance(data, dict):
            raise LLMResponseError("Turn plan must be a JSON object.", error_code="invalid_model_output")
        legacy_intent = _optional_text(data.get("intent"))
        goal = _optional_text(data.get("goal"))
        target = _optional_text(data.get("target"))
        if goal is None and legacy_intent is not None:
            goal, target = {
                "chat": ("chat", "none"),
                "catalog": ("information", "catalog"),
                "recommendation": ("selection", "catalog"),
                "product_detail": ("information", "product"),
                "product_comparison": ("information", "product"),
            }.get(legacy_intent, (None, None))
        if goal not in PLAN_GOALS or target not in PLAN_TARGETS:
            raise LLMResponseError("Turn plan must contain valid goal and target values.", error_code="invalid_model_output")

        reply = _optional_text(data.get("customer_reply"))
        raw_requirement = data.get("requirement")
        requirement = None
        if raw_requirement is not None:
            requirement = ShoppingRequirement.from_dict(raw_requirement)

        operations = data.get("catalog_operations", [])
        if operations is None:
            operations = []
        if not isinstance(operations, list) or not all(isinstance(item, str) for item in operations):
            raise LLMResponseError("catalog_operations must be an array of strings.", error_code="invalid_model_output")
        operations = _deduplicate(item.strip() for item in operations if item.strip())
        if any(operation not in CATALOG_OPERATIONS for operation in operations):
            raise LLMResponseError("Turn plan contains an unsupported catalog operation.", error_code="invalid_model_output")

        state_action = _optional_text(data.get("state_action")) or "none"
        if state_action not in {"none", "merge", "replace"}:
            raise LLMResponseError("Turn plan has an invalid state_action.", error_code="invalid_model_output")
        selection_mode = _optional_text(data.get("selection_mode"))
        if selection_mode is None and goal == "selection" and legacy_intent is not None:
            selection_mode = "criteria"
        if selection_mode not in {None, "criteria", "explicitly_open"}:
            raise LLMResponseError("Turn plan has an invalid selection_mode.", error_code="invalid_model_output")
        action = _optional_text(data.get("action"))
        evidence = data.get("goal_evidence", [])
        if evidence is None:
            evidence = []
        if not isinstance(evidence, list) or not all(isinstance(item, str) for item in evidence):
            raise LLMResponseError("goal_evidence must be an array of strings.", error_code="invalid_model_output")
        evidence = _deduplicate(item.strip() for item in evidence if item.strip())

        if goal == "chat":
            if target != "none" or not reply or requirement is not None or operations or state_action != "none" or action:
                raise LLMResponseError("A chat plan may only contain a customer reply.", error_code="invalid_model_output")
        elif goal == "selection":
            if target != "catalog" or reply is not None or requirement is None or operations or state_action not in {"merge", "replace"} or action or selection_mode is None:
                raise LLMResponseError("A selection plan must contain requirements and a valid state transition.", error_code="invalid_model_output")
        elif goal == "information":
            if reply is not None or state_action != "none" or action:
                raise LLMResponseError("An information plan may not reply directly or mutate state.", error_code="invalid_model_output")
            if target == "catalog" and (requirement is None or not operations):
                raise LLMResponseError("Catalog information requires filters and operations.", error_code="invalid_model_output")
            if target != "catalog" and (requirement is not None or operations):
                raise LLMResponseError("Product information plan contains incompatible fields.", error_code="invalid_model_output")
        else:  # action
            if target != "transaction" or reply is not None or requirement is not None or operations or state_action != "none" or action not in TRANSACTION_ACTIONS:
                raise LLMResponseError("An action plan must request a supported action vocabulary.", error_code="invalid_model_output")
        return cls(goal, target, reply, requirement, operations, state_action, selection_mode, action, evidence)

    @property
    def intent(self) -> str:
        """Compatibility label for trace consumers while the runtime dispatches on goal/target."""
        if self.goal == "chat":
            return "chat"
        if self.goal == "selection":
            return "recommendation"
        if self.goal == "action":
            return "action"
        return "catalog" if self.target == "catalog" else "product_information"


@dataclass
class ConversationEvent:
    """An immutable-style record of one user, state, or assistant action."""

    turn: int
    event_type: str
    payload: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TaskContext:
    """Explicit, compact task state kept alongside the replayable requirement log.

    Requirements answer *what* the user is shopping for.  This context answers
    *where* the conversation is in the workflow, without letting a read-only
    catalog question overwrite an unfinished selection task.
    """

    active_task: str = "none"  # none | selection | information | action
    selection_phase: str = "idle"  # idle | collecting | exploring | recommended | no_match
    selected_product_id: str | None = None
    candidate_product_ids: list[str] = field(default_factory=list)
    last_information_target: str | None = None  # catalog | product | comparison
    last_information_operations: list[str] = field(default_factory=list)
    last_action: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ConversationState:
    """Serializable transcript plus independent selection and catalog-query contexts."""

    conversation_id: str = field(default_factory=lambda: uuid4().hex[:12])
    events: list[ConversationEvent] = field(default_factory=list)
    pending_question: str | None = None
    pending_fields: list[str] = field(default_factory=list)
    status: str = "collecting"
    turn_count: int = 0
    last_catalog_context: dict[str, Any] | None = None
    task_context: TaskContext = field(default_factory=TaskContext)

    def add_event(self, event_type: str, payload: dict[str, Any]) -> None:
        self.events.append(ConversationEvent(self.turn_count, event_type, payload))

    def to_dict(self) -> dict[str, Any]:
        return {
            "conversation_id": self.conversation_id,
            "events": [event.to_dict() for event in self.events],
            "pending_question": self.pending_question,
            "pending_fields": list(self.pending_fields),
            "status": self.status,
            "turn_count": self.turn_count,
            "last_catalog_context": dict(self.last_catalog_context or {}),
            "task_context": self.task_context.to_dict(),
        }


@dataclass
class GroundedRequirement:
    """Validated catalog values used by retrieval; only these values can filter products."""

    item_type: str | None = None
    hard_manufacturer: str | None = None
    preferred_manufacturer: str | None = None
    price_operator: str | None = None
    price_value: float | None = None
    min_price: float | None = None
    max_price: float | None = None
    min_price_inclusive: bool = True
    max_price_inclusive: bool = True
    required_tags: list[str] = field(default_factory=list)
    preferred_tags: list[str] = field(default_factory=list)
    # Values in one group are alternative catalog mappings for the same user
    # concept (OR). Different groups remain independent requirements (AND).
    # The flat fields above are retained for compatible traces and display.
    required_tag_groups: list[list[str]] = field(default_factory=list)
    preferred_tag_groups: list[list[str]] = field(default_factory=list)
    semantic_preferences: list[str] = field(default_factory=list)
    unresolved_hard_constraints: list[str] = field(default_factory=list)
    mappings: list[dict[str, Any]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class PurchaseDecision:
    """Auditable output of the deterministic candidate-ranking policy."""

    purchased_product_id: str | None
    reason: str
    tradeoffs: list[str] = field(default_factory=list)
    confidence: str = "medium"
    match_level: str = "exact_match"

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PurchaseDecision":
        match_level = _optional_text(data.get("match_level")) or "exact_match"
        if match_level not in {"exact_match", "closest_alternative"}:
            match_level = "closest_alternative"
        return cls(
            purchased_product_id=_optional_text(data.get("purchased_product_id")),
            reason=_optional_text(data.get("reason")) or "No explanation was returned.",
            tradeoffs=_string_list(data.get("tradeoffs")),
            confidence=_optional_text(data.get("confidence")) or "medium",
            match_level=match_level,
        )


# ===== product_repository.py =====

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable



GENERIC_CONCEPT_WORDS = {
    "a", "an", "and", "about", "for", "from", "in", "item", "of", "on", "product",
    "related", "style", "styled", "suitable", "theme", "themed", "to", "with",
}

# The catalog itself is English.  These are deliberately small, reviewable aliases for
# common Chinese user expressions; they are not a general translation dictionary.
CATALOG_ITEM_TYPE_ALIASES: dict[str, tuple[str, ...]] = {
    "mug": ("马克杯", "杯子", "咖啡杯", "水杯"),
    "shirt": ("T恤", "t恤", "体恤", "衬衫", "上衣"),
}

CATALOG_TAG_ALIASES: dict[str, tuple[str, ...]] = {
    "Ocean": ("海洋", "大海", "海洋主题"),
    "Beach": ("沙滩", "海滩", "沙滩主题"),
    "Sky": ("天空", "蓝天"),
    "Blue": ("蓝色", "蓝色主题"),
    "Nature": ("自然", "大自然", "自然主题"),
    "Forest": ("森林", "森林主题"),
    "Mountain": ("山", "山景", "高山"),
    "Flowers": ("花卉", "鲜花", "花朵"),
    "Coffee": ("咖啡", "咖啡主题"),
    "Camping": ("露营", "露营主题"),
    "Space": ("太空", "宇宙", "太空主题"),
    "City": ("城市", "都市", "城市主题"),
    "Architecture": ("建筑", "建筑主题"),
    "Vintage": ("复古", "怀旧", "复古风"),
    "Sports": ("运动", "体育", "运动主题"),
    "Winter": ("冬天", "冬季"),
    "Summer": ("夏天", "夏季"),
    "Autumn": ("秋天", "秋季"),
    "Sunset": ("日落", "夕阳"),
    "Sunrise": ("日出", "朝阳"),
    "Animals": ("动物", "动物主题"),
    "Cat": ("猫", "猫咪"),
    "Dog": ("狗", "小狗"),
    "Clothes": ("服装", "服饰", "服装主题"),
}


def _bilingual_alias_catalog(catalog: dict[str, list[str]]) -> dict[str, dict[str, list[str]]]:
    """Expose only aliases whose English canonical value is truly in this catalog."""
    item_types = set(catalog["item_types"])
    tags = set(catalog["tags"])
    return {
        "item_type_aliases": {
            canonical: list(aliases)
            for canonical, aliases in CATALOG_ITEM_TYPE_ALIASES.items()
            if canonical in item_types
        },
        "tag_aliases": {
            canonical: list(aliases)
            for canonical, aliases in CATALOG_TAG_ALIASES.items()
            if canonical in tags
        },
    }


def _normalize(value: str) -> str:
    """Case/punctuation-insensitive normalization that retains non-English letters."""
    return re.sub(r"[^\w]+", " ", value.casefold()).replace("_", " ").strip()


def _meaningful_tokens(value: str) -> set[str]:
    return {token for token in _normalize(value).split() if token not in GENERIC_CONCEPT_WORDS}


def _deduplicate(values: Iterable[str]) -> list[str]:
    seen: set[str] = set()
    result = []
    for value in values:
        key = _normalize(value)
        if key and key not in seen:
            seen.add(key)
            result.append(value)
    return result


def _deduplicate_tag_groups(groups: Iterable[Iterable[str]]) -> list[list[str]]:
    """Keep OR groups intact while removing repeated values and duplicate groups."""
    seen: set[tuple[str, ...]] = set()
    result: list[list[str]] = []
    for group in groups:
        values = _deduplicate(str(value) for value in group)
        key = tuple(sorted(_normalize(value) for value in values))
        if values and key not in seen:
            seen.add(key)
            result.append(values)
    return result


@dataclass(frozen=True)
class Product:
    product_id: str
    name: str
    item_type: str
    manufacturer: str
    price: float
    tags: list[str]
    description: str

    @classmethod
    def from_dict(cls, raw: dict) -> "Product":
        return cls(
            product_id=str(raw["product_id"]),
            name=str(raw["name"]),
            item_type=str(raw["item_type"]),
            manufacturer=str(raw["manufacturer"]),
            price=float(raw["price"]),
            tags=[str(tag) for tag in raw.get("tags", [])],
            description=str(raw.get("description", "")),
        )

    def to_dict(self) -> dict:
        return {
            "product_id": self.product_id,
            "name": self.name,
            "item_type": self.item_type,
            "manufacturer": self.manufacturer,
            "price": self.price,
            "tags": self.tags,
            "description": self.description,
        }


class ProductRepository:
    def __init__(self, data_dir: str | Path):
        data_path = Path(data_dir) / "products.jsonl"
        if not data_path.is_file():
            raise FileNotFoundError(f"Product data was not found: {data_path}")
        self.products = self._load(data_path)
        self.by_id = {product.product_id: product for product in self.products}
        self._catalog = {
            "item_types": sorted({product.item_type for product in self.products}),
            "manufacturers": sorted({product.manufacturer for product in self.products}),
            "tags": sorted({tag for product in self.products for tag in product.tags}),
        }

    @staticmethod
    def _load(path: Path) -> list[Product]:
        with path.open("r", encoding="utf-8") as handle:
            return [Product.from_dict(json.loads(line)) for line in handle if line.strip()]

    def catalog(self) -> dict[str, list[str]]:
        """Return a copy so prompts cannot mutate the repository vocabulary."""
        return {field: list(values) for field, values in self._catalog.items()}

    def ground(self, requirement: ShoppingRequirement) -> GroundedRequirement:
        """Map model semantics to catalog values and preserve every mapping for the trace."""
        grounded = GroundedRequirement(
            price_operator=requirement.price_constraint.operator,
            price_value=requirement.price_constraint.value,
            min_price=requirement.price_constraint.min_value,
            max_price=requirement.price_constraint.max_value,
            min_price_inclusive=requirement.price_constraint.min_inclusive,
            max_price_inclusive=requirement.price_constraint.max_inclusive,
        )

        item_type = self._ground_scalar(
            requirement.item_type, self._catalog["item_types"], allow_lexical_match=True
        )
        self._record_scalar_mapping(grounded, "item_type", requirement.item_type, item_type)
        if requirement.item_type.raw_value:
            if item_type:
                grounded.item_type = item_type
            elif requirement.item_type.constraint_strength == "hard":
                grounded.unresolved_hard_constraints.append(
                    f"商品类别“{requirement.item_type.raw_value}”无法映射到商品库类别"
                )

        manufacturer = self._ground_scalar(
            requirement.manufacturer, self._catalog["manufacturers"], allow_lexical_match=False
        )
        self._record_scalar_mapping(grounded, "manufacturer", requirement.manufacturer, manufacturer)
        if requirement.manufacturer.raw_value:
            if manufacturer and requirement.manufacturer.constraint_strength == "hard":
                grounded.hard_manufacturer = manufacturer
            elif manufacturer:
                grounded.preferred_manufacturer = manufacturer
            elif requirement.manufacturer.constraint_strength == "hard":
                grounded.unresolved_hard_constraints.append(
                    f"厂商“{requirement.manufacturer.raw_value}”不在商品库中"
                )

        for concept in requirement.concepts:
            matched_tags, match_source = self._ground_concept(concept)
            grounded.mappings.append(
                {
                    "field": "concept",
                    "raw_value": concept.raw_value,
                    "kind": concept.kind,
                    "constraint_strength": concept.constraint_strength,
                    "canonical_field": "tags" if matched_tags else None,
                    "canonical_values": matched_tags,
                    "match_source": match_source,
                }
            )
            if concept.constraint_strength == "hard":
                if matched_tags:
                    # One natural-language concept may resolve to several
                    # catalog alternatives (for example Strawberry OR
                    # Strawberries). Do not flatten them into an accidental
                    # conjunction during retrieval.
                    grounded.required_tag_groups.append(matched_tags)
                    grounded.required_tags.extend(matched_tags)
                else:
                    grounded.unresolved_hard_constraints.append(
                        f"硬性条件“{concept.raw_value}”无法映射到商品库标签"
                    )
            else:
                grounded.semantic_preferences.append(concept.raw_value)
                if matched_tags:
                    grounded.preferred_tag_groups.append(matched_tags)
                grounded.preferred_tags.extend(matched_tags)

        grounded.required_tags = _deduplicate(grounded.required_tags)
        grounded.preferred_tags = _deduplicate(grounded.preferred_tags)
        grounded.required_tag_groups = _deduplicate_tag_groups(grounded.required_tag_groups)
        grounded.preferred_tag_groups = _deduplicate_tag_groups(grounded.preferred_tag_groups)
        grounded.semantic_preferences = _deduplicate(grounded.semantic_preferences)
        grounded.unresolved_hard_constraints = _deduplicate(grounded.unresolved_hard_constraints)
        return grounded

    def retrieve(self, requirement: GroundedRequirement) -> tuple[list[Product], dict[str, int | list[str]]]:
        initial = self.products
        if requirement.unresolved_hard_constraints:
            return [], {
                "total_products": len(initial),
                "after_item_type": 0,
                "after_hard_manufacturer": 0,
                "after_price": 0,
                "after_required_tags": 0,
                "unresolved_hard_constraints": requirement.unresolved_hard_constraints,
            }

        after_type = [
            product
            for product in initial
            if not requirement.item_type or product.item_type == requirement.item_type
        ]
        after_manufacturer = [
            product
            for product in after_type
            if not requirement.hard_manufacturer
            or product.manufacturer == requirement.hard_manufacturer
        ]
        after_price = [
            product for product in after_manufacturer if self._matches_price(product, requirement)
        ]
        after_tags = [
            product
            for product in after_price
            if self._has_tag_groups(product, requirement.required_tag_groups, requirement.required_tags)
        ]
        ranked = self.rank_candidates(after_tags, requirement)
        return ranked, {
            "total_products": len(initial),
            "after_item_type": len(after_type),
            "after_hard_manufacturer": len(after_manufacturer),
            "after_price": len(after_price),
            "after_required_tags": len(after_tags),
            "unresolved_hard_constraints": [],
        }

    # Hard constraints in the order they are worth relaxing.  Item type is absent
    # on purpose: `mug` and `shirt` are disjoint, so dropping it does not produce a
    # near miss, it changes the request into a different one.
    RELAXABLE_CONSTRAINTS = ("price", "manufacturer", "tags")

    def closest_alternatives(
        self, requirement: GroundedRequirement, *, limit: int = 3
    ) -> list[dict[str, Any]]:
        """Find near misses by dropping exactly one hard constraint at a time.

        Returns one entry per constraint whose removal yields products, so the
        caller can tell the user *which* condition blocked the search and what it
        would cost to relax it.  Nothing here guesses: every product returned is
        verified against all the remaining constraints.
        """
        if requirement.unresolved_hard_constraints:
            # The blocker is a value the catalog does not contain at all, so
            # relaxing a different condition cannot rescue this query.
            return []
        alternatives: list[dict[str, Any]] = []
        for constraint in self.RELAXABLE_CONSTRAINTS:
            if not self._constrains(requirement, constraint):
                continue
            relaxed = self._without_constraint(requirement, constraint)
            products, _ = self.retrieve(relaxed)
            if not products:
                continue
            if constraint == "price":
                products = sorted(
                    products,
                    key=lambda product: (
                        self._price_distance(product.price, requirement),
                        product.price,
                        product.product_id,
                    ),
                )
            alternatives.append(
                {
                    "relaxed_constraint": constraint,
                    "match_count": len(products),
                    "products": [product.to_dict() for product in products[:limit]],
                    "gap": self._constraint_gap(requirement, constraint, products[0]),
                }
            )
        return alternatives

    @staticmethod
    def _constrains(requirement: GroundedRequirement, constraint: str) -> bool:
        if constraint == "price":
            return bool(
                requirement.price_value is not None
                or requirement.min_price is not None
                or requirement.max_price is not None
            )
        if constraint == "manufacturer":
            return bool(requirement.hard_manufacturer)
        return bool(requirement.required_tag_groups or requirement.required_tags)

    @staticmethod
    def _without_constraint(
        requirement: GroundedRequirement, constraint: str
    ) -> GroundedRequirement:
        relaxed = GroundedRequirement(**asdict(requirement))
        if constraint == "price":
            relaxed.price_operator = None
            relaxed.price_value = None
            relaxed.min_price = None
            relaxed.max_price = None
        elif constraint == "manufacturer":
            relaxed.hard_manufacturer = None
        else:
            relaxed.required_tags = []
            relaxed.required_tag_groups = []
        return relaxed

    @staticmethod
    def _constraint_gap(
        requirement: GroundedRequirement, constraint: str, closest: Product
    ) -> dict[str, Any]:
        """Quantify how far the nearest product misses, using catalog values only."""
        if constraint == "price":
            return {
                "requested": ProductRepository._price_description(requirement),
                "actual": f"${closest.price:.2f}",
                "difference": round(
                    ProductRepository._price_distance(closest.price, requirement), 2
                ),
            }
        if constraint == "manufacturer":
            return {
                "requested": requirement.hard_manufacturer,
                "actual": closest.manufacturer,
            }
        groups = requirement.required_tag_groups or [[tag] for tag in requirement.required_tags]
        return {
            "requested": [" 或 ".join(group) for group in groups],
            "actual": closest.tags,
        }

    @staticmethod
    def _price_distance(price: float, requirement: GroundedRequirement) -> float:
        """Distance from a price interval; zero means the price already fits it."""
        if requirement.min_price is not None and price < requirement.min_price:
            return requirement.min_price - price
        if requirement.max_price is not None and price > requirement.max_price:
            return price - requirement.max_price
        if requirement.price_value is None:
            return 0.0
        value = requirement.price_value
        if requirement.price_operator in {"<", "<="}:
            return max(0.0, price - value)
        if requirement.price_operator in {">", ">="}:
            return max(0.0, value - price)
        return abs(price - value)

    @staticmethod
    def _price_description(requirement: GroundedRequirement) -> str:
        if requirement.min_price is not None and requirement.max_price is not None:
            lower = "≤" if requirement.min_price_inclusive else "<"
            upper = "≤" if requirement.max_price_inclusive else "<"
            return f"${requirement.min_price:.2f} {lower} 价格 {upper} ${requirement.max_price:.2f}"
        if requirement.min_price is not None:
            operator = "≥" if requirement.min_price_inclusive else ">"
            return f"价格 {operator} ${requirement.min_price:.2f}"
        if requirement.max_price is not None:
            operator = "≤" if requirement.max_price_inclusive else "<"
            return f"价格 {operator} ${requirement.max_price:.2f}"
        return f"价格 {requirement.price_operator} ${requirement.price_value:.2f}"

    def tags_in_text(self, text: str) -> list[str]:
        """Match English catalog tags plus an explicit, verified Chinese alias subset."""
        tokens = _meaningful_tokens(text)
        matched = [
            tag
            for tag in self._catalog["tags"]
            if (tag_tokens := _meaningful_tokens(tag)) and tag_tokens.issubset(tokens)
        ]
        for canonical, aliases in CATALOG_TAG_ALIASES.items():
            if canonical not in self._catalog["tags"]:
                continue
            if any(alias.casefold() in text.casefold() for alias in aliases):
                matched.append(canonical)
        return _deduplicate(matched)

    def _ground_concept(self, concept: Concept) -> tuple[list[str], str]:
        validated_hints = [
            canonical
            for hint in concept.catalog_tag_hints
            if (canonical := self._exact_catalog_value(hint, self._catalog["tags"])) is not None
        ]
        if validated_hints:
            return _deduplicate(validated_hints), "model_catalog_hint"

        lexical = self.tags_in_text(concept.raw_value)
        if lexical:
            return lexical, "lexical_catalog_match"
        return [], "unresolved"

    def _ground_scalar(
        self,
        constraint: CatalogConstraint,
        allowed: list[str],
        *,
        allow_lexical_match: bool,
    ) -> str | None:
        if constraint.catalog_hint:
            canonical = self._exact_catalog_value(constraint.catalog_hint, allowed)
            if canonical:
                return canonical
        if not constraint.raw_value:
            return None
        canonical = self._exact_catalog_value(constraint.raw_value, allowed)
        if canonical or not allow_lexical_match:
            return canonical

        raw_tokens = _meaningful_tokens(constraint.raw_value)
        matches = [
            value
            for value in allowed
            if (value_tokens := _meaningful_tokens(value)) and value_tokens.issubset(raw_tokens)
        ]
        return matches[0] if len(matches) == 1 else None

    @staticmethod
    def _exact_catalog_value(value: str, allowed: list[str]) -> str | None:
        normalized = _normalize(value)
        return next((candidate for candidate in allowed if _normalize(candidate) == normalized), None)

    @staticmethod
    def _record_scalar_mapping(
        grounded: GroundedRequirement,
        field: str,
        constraint: CatalogConstraint,
        canonical: str | None,
    ) -> None:
        grounded.mappings.append(
            {
                "field": field,
                "raw_value": constraint.raw_value,
                "constraint_strength": constraint.constraint_strength,
                "canonical_field": field if canonical else None,
                "canonical_values": [canonical] if canonical else [],
                "match_source": "model_catalog_hint" if constraint.catalog_hint and canonical else (
                    "lexical_catalog_match" if canonical else "unresolved"
                ),
            }
        )

    @staticmethod
    def _has_tag_groups(
        product: Product, groups: Iterable[Iterable[str]], legacy_tags: Iterable[str]
    ) -> bool:
        """Apply OR within each concept group and AND between concept groups.

        ``legacy_tags`` preserves compatibility with callers that construct a
        GroundedRequirement directly instead of passing it through ground().
        """
        product_tags = {_normalize(tag) for tag in product.tags}
        resolved_groups: list[list[str]] = []
        for group in groups:
            values = list(group)
            if values:
                resolved_groups.append(values)
        if not resolved_groups:
            resolved_groups = [[tag] for tag in legacy_tags]
        return all(
            any(_normalize(tag) in product_tags for tag in group)
            for group in resolved_groups
        )

    @staticmethod
    def _matches_price(product: Product, requirement: GroundedRequirement) -> bool:
        if requirement.min_price is not None:
            if requirement.min_price_inclusive and product.price < requirement.min_price:
                return False
            if not requirement.min_price_inclusive and product.price <= requirement.min_price:
                return False
        if requirement.max_price is not None:
            if requirement.max_price_inclusive and product.price > requirement.max_price:
                return False
            if not requirement.max_price_inclusive and product.price >= requirement.max_price:
                return False
        if requirement.min_price is not None or requirement.max_price is not None:
            return True
        if requirement.price_operator is None or requirement.price_value is None:
            return True
        value = requirement.price_value
        return {
            "<": product.price < value,
            "<=": product.price <= value,
            "=": product.price == value,
            ">=": product.price >= value,
            ">": product.price > value,
        }[requirement.price_operator]

    @staticmethod
    def preference_score(product: Product, requirement: GroundedRequirement) -> tuple[int, int]:
        product_tags = {_normalize(tag) for tag in product.tags}
        groups = requirement.preferred_tag_groups or [[tag] for tag in requirement.preferred_tags]
        preferred_tag_score = sum(
            any(_normalize(tag) in product_tags for tag in group)
            for group in groups
        )
        preferred_manufacturer_score = int(product.manufacturer == requirement.preferred_manufacturer)
        return preferred_manufacturer_score, preferred_tag_score

    @classmethod
    def same_soft_preference_score(
        cls, first: Product, second: Product, requirement: GroundedRequirement
    ) -> bool:
        return cls.preference_score(first, requirement) == cls.preference_score(second, requirement)

    @classmethod
    def ranking_key(
        cls, product: Product, requirement: GroundedRequirement
    ) -> tuple[int, int, float, str]:
        """Stable policy after hard filtering: preferences, price, then product ID."""
        preferred_manufacturer, preferred_tags = cls.preference_score(product, requirement)
        return (-preferred_manufacturer, -preferred_tags, product.price, product.product_id)

    @classmethod
    def rank_candidates(
        cls, products: Iterable[Product], requirement: GroundedRequirement
    ) -> list[Product]:
        return sorted(products, key=lambda product: cls.ranking_key(product, requirement))

    @staticmethod
    def _ranked_counts(counts: dict[str, int], limit: int) -> list[dict[str, Any]]:
        ordered = sorted(counts.items(), key=lambda pair: (-pair[1], pair[0]))
        return [{"value": value, "count": count} for value, count in ordered[:limit]]

    @staticmethod
    def _price_bands(by_price: list[Product], bands: int = 3) -> list[dict[str, Any]]:
        """Split an already price-sorted list into contiguous, non-empty bands."""
        if len(by_price) < bands:
            bands = 1
        size = (len(by_price) + bands - 1) // bands
        result: list[dict[str, Any]] = []
        for start in range(0, len(by_price), size):
            chunk = by_price[start : start + size]
            if chunk:
                result.append(
                    {
                        "low": chunk[0].price,
                        "high": chunk[-1].price,
                        "count": len(chunk),
                    }
                )
        return result

    def catalog_highlights(
        self,
        products: list[Product],
        *,
        top_values: int = 5,
        sample_size: int = 3,
    ) -> dict[str, Any]:
        """Summarize a product set so the agent can guide the user's next turn.

        Read-only and deterministic: facets are ordered by (-count, value) and
        samples by (price, product_id), so the same product set always yields the
        same guidance.  No model call is involved.
        """
        if not products:
            return {
                "count": 0,
                "price_bands": [],
                "top_tags": [],
                "top_manufacturers": [],
                "sample_products": [],
            }
        tag_counts: dict[str, int] = {}
        manufacturer_counts: dict[str, int] = {}
        item_type_counts: dict[str, int] = {}
        for product in products:
            manufacturer_counts[product.manufacturer] = (
                manufacturer_counts.get(product.manufacturer, 0) + 1
            )
            item_type_counts[product.item_type] = item_type_counts.get(product.item_type, 0) + 1
            for tag in product.tags:
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
        by_price = sorted(products, key=lambda product: (product.price, product.product_id))
        return {
            "count": len(products),
            "price_min": by_price[0].price,
            "price_max": by_price[-1].price,
            "price_bands": self._price_bands(by_price),
            "top_tags": self._ranked_counts(tag_counts, top_values),
            "top_manufacturers": self._ranked_counts(manufacturer_counts, top_values),
            "item_types": self._ranked_counts(item_type_counts, top_values),
            "sample_products": [product.to_dict() for product in by_price[:sample_size]],
        }


# ===== prompts.py =====

import json
from typing import Any


TURN_PLANNER_SYSTEM_PROMPT = """You are the single-turn planner for a Chinese shopping customer-service agent.
Translate exactly the latest customer message into one valid JSON object and no Markdown. Python, not you,
will execute catalog access, state updates, and product selection. The exact keys are:

goal (chat | information | selection | action), target (none | catalog | product | transaction),
customer_reply (string or null), requirement (object or null), catalog_operations (array),
state_action (none | merge | replace), selection_mode (criteria | explicitly_open | null),
action (order.create | order.cancel | payment.create | null), goal_evidence (array of exact user substrings).

The input includes intent_signals to help you classify ambiguous cases. Pay attention to:
- is_likely_comparison: true when user has 2+ product IDs + comparison words
- is_likely_catalog_query: true when user asks availability/price range without purchase intent
- is_likely_transaction: true when user has product ID + transaction verbs

## Disambiguation Examples

**Catalog query (not selection):**
- "你家有Ocean主题的马克杯吗？" → goal=information, target=catalog, operations=[count, group_by_tag]
  (has "有吗", no purchase verb like "想买")
- "衬衫都有什么价位？" → goal=information, target=catalog, operations=[price_range]
  (asks about price range, not "need a shirt under $X")
- "最便宜的马克杯多少钱？" → goal=information, target=catalog, operations=[price_extreme]
  (asks fact about cheapest, not "buy the cheapest")

**Selection (not catalog query):**
- "我想买一个Ocean主题的马克杯" → goal=selection (has purchase intent: "想买")
- "推荐一件T恤，预算30以内" → goal=selection (has "推荐")
- "需要一个便宜的马克杯" → goal=selection (has "需要")

**Product detail (not transaction):**
- "P0005是什么商品？" → goal=information, target=product
- "P0005多少钱？" → goal=information, target=product (asks about product, not ordering it)
- "比较P0005和P0006" → goal=information, target=product (is_likely_comparison=true)

**Transaction (not product detail):**
- "下单P0005" → goal=action, target=transaction, action=order.create (has "下单")
- "我要购买P1234" → goal=action (has transaction verb: "购买")
- "支付P0005" → goal=action, action=payment.create

Key verb signals: "有吗/都有什么/多少钱" = catalog query; "想买/推荐/需要" = selection; "下单/购买/支付" = transaction.

Use goal=chat and target=none only when the latest message needs no catalog fact or external action. Set
customer_reply to concise natural Chinese, requirement to null, catalog_operations to [], state_action to
"none", selection_mode/action to null. Answer the latest message only;
do not mention a previous failure or shopping condition unless the latest message explicitly refers to it.

Use goal=information and target=catalog when the user asks verifiable local-catalog facts. Set customer_reply
to null, state_action to "none", action/selection_mode to null, and provide requirement as filters.
catalog_operations is one or more of: count,
group_by_item_type, group_by_manufacturer, group_by_tag, list, price_range, price_extreme.
Choose every operation needed by a compound question. Availability needs count; "which styles" or "which
tags" needs group_by_tag; a question can need both. Catalog queries never change a pending recommendation.
When a short catalog follow-up omits its scope (for example "价位呢？"), use last_catalog_context only as
the scope of that read-only query; never merge it into active_selection_context.
task_context describes the current workflow phase and focus product(s). Use it only to resolve an
elliptical follow-up; do not treat a prior recommendation as a new purchase request by itself.

Use goal=selection and target=catalog only when the user explicitly asks to choose/recommend a product, or
clearly answers a pending selection question. Set customer_reply to null, catalog_operations to [],
and provide requirement. Use state_action="merge" only to refine the current selection; use
state_action="replace" when the latest request clearly starts a new selection, especially when it names a
different product type. If the latest message says it no longer wants one product type and switches to
another (for example “不要杯子，改成 shirt” or “not a mug, switch to a shirt”), retain only the new
type and use state_action="replace". selection_mode is "criteria" when the user specifies selection criteria, or
"explicitly_open" only when they clearly permit an unconstrained/default choice.
goal_evidence must quote the exact user substring that authorizes selection; when continuing a pending
selection question, use an empty array.

Use goal=information and target=product for an explicit product-detail question or comparison. All fields
except goal, target, goal_evidence must be null/empty and state_action must be "none". Use goal=action and
target=transaction for order, payment, or cancellation requests, set action to the matching operation, and
leave all other fields null/empty. Never convert a transaction request containing a product ID into product
detail or selection. Product IDs are validated by Python.

requirement is null except for catalog information and selection. When present, its exact keys are item_type,
manufacturer, price_constraint, concepts, needs_clarification, clarification_question. item_type and
manufacturer are objects with raw_value, constraint_strength, catalog_hint. price_constraint is an object
with either legacy operator/value or min_value/max_value plus min_inclusive/max_inclusive, or null.
Use min_value/max_value for a stated range (for example “10 元以上、20 元以下”); never drop one
of the two bounds. Each concept has raw_value, kind, constraint_strength, catalog_tag_hints.
constraint_strength is hard or preference. Product type and budget are hard. Style, visual motif, aesthetics,
use case, and suitability are preferences only when the user expresses them as a preference (for example
"喜欢", "优先", "prefer"). When a theme or style directly describes the item requested in the main clause
(for example "想买纽约风的衣服" or "a Beach themed mug"), it is hard even without words such as
"必须". A catalog query treats its stated
filters as hard in execution. catalog_hint and catalog_tag_hints must be exact supplied catalog values or
null/an empty list; never invent catalog facts. Multiple catalog_tag_hints inside one concept are alternative
catalog mappings for that one concept, not several separate user requirements. Preserve Chinese raw wording
and use supplied bilingual aliases only when their English canonical value exists in the catalog.

Do not invent inventory, orders, delivery, returns, policies, product IDs, or facts outside the supplied catalog.
"""


def turn_planner_messages(
    instruction: str,
    catalog: dict[str, list[str]],
    conversation_context: dict[str, Any],
    shopping_context: dict[str, Any],
    catalog_context: dict[str, Any] | None = None,
    intent_signals: dict[str, Any] | None = None,
) -> list[dict[str, str]]:
    payload = {
        "latest_user_message": instruction,
        "recent_customer_messages": conversation_context,
        "active_selection_context": shopping_context,
        "last_catalog_context": catalog_context or {},
        "catalog": catalog,
        "bilingual_aliases": _bilingual_alias_catalog(catalog),
        "intent_signals": intent_signals or {},
    }
    return [
        {"role": "system", "content": TURN_PLANNER_SYSTEM_PROMPT},
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


def turn_plan_repair_messages(raw_plan: Any, validation_error: str) -> list[dict[str, str]]:
    """Ask the same API for one bounded protocol-only repair before any side effect."""
    payload = {
        "invalid_plan": raw_plan,
        "validation_error": validation_error,
        "required_contract": {
            "goal": ["chat", "information", "selection", "action"],
            "target": ["none", "catalog", "product", "transaction"],
            "state_action": ["none", "merge", "replace"],
            "selection_mode": ["criteria", "explicitly_open", None],
            "action": ["order.create", "order.cancel", "payment.create", None],
        },
    }
    return [
        {
            "role": "system",
            "content": (
                "Repair the invalid shopping-agent plan into exactly one JSON object and no Markdown. "
                "Do not answer the customer, invent catalog facts, or change the intended goal. "
                "Return only a plan that satisfies the supplied contract."
            ),
        },
        {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
    ]


# ===== llm_client.py =====

import json
import re
from typing import Any



class LLMResponseError(RuntimeError):
    """Raised when a model response cannot be used safely, with a stable public error class."""

    def __init__(self, message: str, *, error_code: str = "model_response_error"):
        super().__init__(message)
        self.error_code = error_code


class DeepSeekClient:
    def __init__(self, settings: Settings):
        try:
            from openai import OpenAI
        except ImportError as exc:
            raise LLMResponseError(
                "The 'openai' package is not installed. Run pip install -r requirements.txt."
            ) from exc

        self._client = OpenAI(
            api_key=settings.api_key,
            base_url="https://api.deepseek.com",
            timeout=settings.timeout_seconds,
            max_retries=settings.max_retries,
        )
        self._model = settings.model

    def chat_json(self, messages: list[dict[str, str]]) -> dict[str, Any]:
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=messages,
                temperature=0.2,
                response_format={"type": "json_object"},
            )
            content = response.choices[0].message.content or ""
        except Exception as exc:  # API library exposes provider-specific exception classes.
            raise LLMResponseError(
                f"DeepSeek request failed: {exc}", error_code=self._error_code(exc)
            ) from exc

        return self._parse_json(content)

    @staticmethod
    def _error_code(exc: Exception) -> str:
        name = type(exc).__name__
        if name == "APITimeoutError":
            return "timeout"
        if name == "APIConnectionError":
            return "connection"
        if name == "AuthenticationError":
            return "authentication"
        if name == "RateLimitError":
            return "rate_limit"
        if name == "APIStatusError":
            return "provider_status"
        return "model_request_error"

    @staticmethod
    def _parse_json(content: str) -> dict[str, Any]:
        cleaned = content.strip()
        fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", cleaned, flags=re.DOTALL)
        if fenced:
            cleaned = fenced.group(1).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise LLMResponseError("Model response was not valid JSON.", error_code="invalid_model_output") from exc
        if not isinstance(data, dict):
            raise LLMResponseError("Model response JSON must be an object.", error_code="invalid_model_output")
        return data


# ===== agent.py =====

import os
import re
from pathlib import Path
from typing import Any



class ShoppingAgent:
    """Fixed, bounded workflow: parse -> retrieve -> filter -> decide -> validate."""

    def __init__(self, data_dir: str | Path):
        self.data_dir = Path(data_dir)
        self.repository = ProductRepository(self.data_dir)
        self._settings_error: str | None = None
        try:
            settings = Settings.from_environment()
            self.llm = DeepSeekClient(settings)
            self.max_candidates = settings.max_candidates
        except (ConfigurationError, LLMResponseError) as exc:
            # A missing or unavailable model is surfaced to the user; no rule-based shopping fallback runs.
            self.llm = None
            self.max_candidates = 8
            self._settings_error = str(exc)

    def run(self, instruction: str) -> dict[str, Any]:
        """Compatibility view over the sole multi-turn execution path, using a fresh state."""
        result = self.run_turn(instruction, ConversationState())
        return {
            key: result[key]
            for key in ("instruction", "purchased_product_id", "trace", "summary")
        }

    def run_turn(
        self, message: str, state: ConversationState | None = None
    ) -> dict[str, Any]:
        """Run one dialogue turn through the configured model API without a local decision fallback."""
        state = state or ConversationState()
        try:
            return self._run_turn(message, state)
        except LLMResponseError as exc:
            message = message.strip()
            # A failed turn must not silently leave partially parsed constraints in memory.
            state.events = [
                event
                for event in state.events
                if not (
                    event.turn == state.turn_count
                    and event.event_type == "constraint_update"
                )
            ]
            trace = list(getattr(exc, "workflow_trace", []))
            trace.append(
                {
                    "step": "model_service",
                    "status": "failed",
                    "error_code": exc.error_code,
                    "warning": str(exc),
                }
            )
            summary = (
                "模型回复未通过工作流协议校验，已尝试一次自动修复但未成功；"
                "本轮未执行商品检索或推荐，请稍后重试。"
                if exc.error_code == "invalid_model_output"
                else "模型服务暂不可用，未执行商品检索或推荐。请检查 API 配置或稍后重试。"
            )
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                summary,
                "service_error",
                update_shopping_state=False,
            )

    def _run_turn(
        self, message: str, state: ConversationState | None = None
    ) -> dict[str, Any]:
        """Handle one bounded dialogue turn while keeping a replayable event log.

        The log is the source of truth.  A reducer derives the current requirement
        from it before each retrieval, so an updated budget or product type replaces
        the prior constraint instead of being appended to a growing prompt.
        """
        state = state or ConversationState()
        message = message.strip()
        state.turn_count += 1
        state.add_event("user_message", {"message": message})
        trace: list[dict[str, Any]] = [
            {"step": "conversation_state", "status": "received", "turn": state.turn_count}
        ]

        if not message:
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                "请先输入购物需求，或回答上一个问题。",
                "clarification",
                pending_question="请告诉我想购买哪类商品：mug（马克杯）还是 shirt（T 恤）？",
                pending_fields=["item_type"],
            )

        if self._looks_like_inverted_price_range(message):
            question = "价格区间的下限不能高于上限。请重新说明，例如“10 元以上、20 元以下”。"
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "input_integrity", "status": "clarification_required", "fields": ["price_constraint"]}],
                question,
                "clarification",
                pending_question=question,
                pending_fields=["price_constraint"],
            )

        previous = self._reduce_requirement(state)
        if self._is_type_agnostic_gift_request(message):
            question = "送礼的话，你想看 mug（马克杯）还是 shirt（T 恤）？也可以补充预算或喜欢的主题。"
            return self._finish_turn(
                state,
                message,
                None,
                trace + [
                    {
                        "step": "deterministic_clarification",
                        "status": "requested",
                        "reason": "gift_without_item_type",
                        "fields": ["item_type"],
                    }
                ],
                question,
                "clarification",
                pending_question=question,
                pending_fields=["item_type"],
            )

        plan = self._active_selection_price_refinement_plan(message, state, previous, trace)
        if plan is None:
            plan = self._create_turn_plan(message, state, previous, trace)
        plan = self._enforce_pending_price_refinement(
            plan, message, state, previous, trace
        )
        if plan.goal == "chat":
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                plan.customer_reply or "我可以继续帮助你挑选商品。",
                "chat",
                update_shopping_state=False,
            )
        if plan.goal == "action":
            return self._handle_action_request(state, message, plan, trace)
        if plan.goal == "information" and plan.target == "catalog":
            return self._handle_catalog_plan(state, message, plan, trace)
        if plan.goal == "information" and plan.target == "product":
            product_ids = self._product_ids_in_message(message)
            if len(product_ids) >= 2:
                return self._handle_product_comparison(state, message, trace)
            return self._handle_product_detail(state, message, trace)
        if plan.goal != "selection":
            error = LLMResponseError("Turn plan could not be dispatched.", error_code="invalid_model_output")
            error.workflow_trace = list(trace)
            raise error

        conflicting_types = self._mentioned_item_types(message)
        if len(conflicting_types) > 1:
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "conflict_detection", "status": "conflict", "fields": ["item_type"]}],
                "同一轮中同时出现了 " + " 和 ".join(conflicting_types)
                + "。这两个商品类型需要分别检索，请先选择本轮要购买的一种。",
                "conflict",
                pending_question="你这次想买 mug（马克杯）还是 shirt（T 恤）？",
                pending_fields=["item_type"],
            )

        if self._looks_like_missing_price(message):
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "input_integrity", "status": "clarification_required"}],
                "检测到价格表达不完整，请补充具体金额，例如“价格低于 $23”。",
                "clarification",
                pending_question="你的预算上限是多少？例如“低于 $23”。",
                pending_fields=["price_constraint"],
            )

        parsed = plan.requirement
        if parsed is None:
            error = LLMResponseError("Recommendation plan did not contain requirements.", error_code="invalid_model_output")
            error.workflow_trace = list(trace)
            raise error
        self._resolve_price_constraint(message, parsed, trace)
        self._enforce_primary_topic_constraints(message, parsed, trace)
        self._clear_generic_item_type(parsed)

        previous_item_type = self._canonical_item_type(previous.item_type)
        parsed_item_type = self._canonical_item_type(parsed.item_type)
        has_item_type_change = (
            parsed.item_type.raw_value
            and previous.item_type.raw_value
            and (
                parsed_item_type != previous_item_type
                if parsed_item_type and previous_item_type
                else _normalize(parsed.item_type.raw_value)
                != _normalize(previous.item_type.raw_value)
            )
        )
        # A selection request that explicitly names one new canonical type is a new task.
        # Only two types in the *same* message are ambiguous; do not require users to know
        # implementation-specific phrases such as "change to".
        replace_selection = has_item_type_change or plan.state_action == "replace"
        if replace_selection:
            trace.append(
                {
                    "step": "selection_transition",
                    "status": "replaced",
                    "previous_item_type": previous.item_type.raw_value,
                    "new_item_type": parsed.item_type.raw_value,
                    "reason": "new_explicit_type" if has_item_type_change else "planner_replace",
                }
            )

        self._append_requirement_updates(
            state, parsed, previous, message, trace, replace_selection=replace_selection
        )
        requirement = self._reduce_requirement(state)
        trace.append(
            {
                "step": "state_reduction",
                "status": "completed",
                "active_requirements": requirement.to_dict(),
            }
        )
        dialogue_stage = DialoguePolicy().selection_stage(requirement, plan.selection_mode)
        trace.append(
            {
                "step": "dialogue_policy",
                "status": "completed",
                "stage": dialogue_stage,
                "reason": "type_only_exploration" if dialogue_stage == "exploring" else "selection_ready",
            }
        )

        # The model may suggest a clarification question, but it must not veto a
        # request the policy considers retrievable.  Otherwise an occasional
        # over-cautious `needs_clarification=true` makes identical requests
        # non-deterministically stop early.
        if not RecommendationPolicy().is_ready(requirement):
            question = "请先告诉我想买哪类商品：mug（马克杯）还是 shirt（T 恤）？"
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "clarification", "status": "requested", "fields": ["item_type"]}],
                question,
                "clarification",
                pending_question=question,
                pending_fields=["item_type"],
            )

        grounded = self.repository.ground(requirement)
        trace.append(
            {
                "step": "catalog_grounding",
                "status": "completed",
                "grounded_requirements": grounded.to_dict(),
            }
        )
        candidates, counts = self.repository.retrieve(grounded)
        trace.append(
            {
                "step": "retrieval_and_hard_filtering",
                "status": "completed",
                "filter_counts": counts,
                "eligible_product_count": len(candidates),
            }
        )
        if not candidates:
            alternatives = self.repository.closest_alternatives(grounded)
            trace.append(
                {
                    "step": "closest_alternative_search",
                    "status": "completed",
                    "handler": "single_constraint_relaxation",
                    "relaxed_constraints": [
                        item["relaxed_constraint"] for item in alternatives
                    ],
                }
            )
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "decision", "status": "no_match"}],
                self._no_match_summary(grounded, alternatives),
                "no_match",
                catalog_data={"kind": "closest_alternatives", "alternatives": alternatives},
            )

        if dialogue_stage == "exploring":
            highlights = self.repository.catalog_highlights(candidates)
            question = "你更在意预算、主题，还是某个厂商？也可以直接告诉我你想优先满足的条件。"
            trace.append(
                {
                    "step": "catalog_exploration",
                    "status": "completed",
                    "sample_product_ids": [item["product_id"] for item in highlights["sample_products"]],
                    "catalog_count": highlights["count"],
                }
            )
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                self._exploration_summary(requirement, highlights, question),
                "exploration",
                pending_question=question,
                pending_fields=["price_constraint", "concept", "manufacturer"],
                catalog_data={
                    "kind": "exploration",
                    "total_count": highlights["count"],
                    "products": highlights["sample_products"],
                    "highlights": highlights,
                },
                guidance_products=candidates,
            )

        decision, selected = self._rank_candidates(grounded, candidates, trace)
        return self._finish_turn(
            state,
            message,
            selected.product_id,
            trace,
            self._format_summary(selected, decision),
            "recommendation",
            guidance_products=candidates,
        )

    def _handle_action_request(
        self,
        state: ConversationState,
        message: str,
        plan: TurnPlan,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Enforce capability boundaries before any external business action could occur."""
        action = plan.action or ""
        capability_status = CAPABILITY_REGISTRY.get(action, "unsupported")
        trace.append(
            {
                "step": "capability_check",
                "status": capability_status,
                "action": action,
                "target_product_ids": self._product_ids_in_message(message),
            }
        )
        if capability_status != "supported":
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                "当前系统支持商品查询、比较和推荐，暂不支持创建订单、支付或取消订单。"
                "我可以为你查看该商品详情或继续比较商品。",
                "capability_unavailable",
                update_shopping_state=False,
                catalog_data={"kind": "capability", "action": action, "status": capability_status},
            )
        error = LLMResponseError("No transaction executor is registered.", error_code="capability_unavailable")
        error.workflow_trace = list(trace)
        raise error

    def _handle_catalog_plan(
        self,
        state: ConversationState,
        message: str,
        plan: TurnPlan,
        trace: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Execute a validated catalog plan without mutating recommendation state."""
        filters = plan.requirement
        if filters is None:
            error = LLMResponseError("Catalog plan did not contain filters.", error_code="invalid_model_output")
            error.workflow_trace = list(trace)
            raise error
        self._enforce_catalog_query_filters(filters)
        state.last_catalog_context = {
            "filters": filters.to_dict(),
            "catalog_operations": list(plan.catalog_operations),
        }
        grounded = self.repository.ground(filters)
        resolution_statuses = self._catalog_resolution_statuses(grounded)
        unresolved_concepts = [
            status["raw_value"]
            for status in resolution_statuses
            if status["field"] == "concept" and status["status"] == "unresolved"
        ]
        ambiguous_statuses = [
            status
            for status in resolution_statuses
            if status["field"] == "concept" and status["status"] == "ambiguous"
        ]
        ambiguous_concepts = [status["raw_value"] for status in ambiguous_statuses]
        # An unknown catalog-query facet means "cannot verify", not "known value with zero matches".
        # Keep independently resolved filters (for example shirt) and use them to show valid alternatives.
        if unresolved_concepts:
            grounded.unresolved_hard_constraints = [
                issue
                for issue in grounded.unresolved_hard_constraints
                if not any(f"“{raw_value}”" in issue for raw_value in unresolved_concepts)
            ]
        # Several canonical tags for one user phrase are an ambiguity, not an AND filter.
        # Remove only the ambiguous phrase's tags, retain independent resolved constraints,
        # and present the verified catalog facets for the user to choose from.
        if ambiguous_statuses:
            ambiguous_tags = {
                value
                for status in ambiguous_statuses
                for value in status["canonical_values"]
            }
            grounded.required_tags = [tag for tag in grounded.required_tags if tag not in ambiguous_tags]
            ambiguous_group_keys = {
                tuple(sorted(_normalize(value) for value in status["canonical_values"]))
                for status in ambiguous_statuses
            }
            grounded.required_tag_groups = [
                group
                for group in grounded.required_tag_groups
                if tuple(sorted(_normalize(value) for value in group)) not in ambiguous_group_keys
            ]
        effective_operations = list(plan.catalog_operations)
        if (unresolved_concepts or ambiguous_concepts) and "group_by_tag" not in effective_operations:
            effective_operations.append("group_by_tag")
        products, counts = self.repository.retrieve(grounded)
        trace.extend(
            [
                {
                    "step": "catalog_query_grounding",
                    "status": "completed",
                    "catalog_operations": effective_operations,
                    "grounded_requirements": grounded.to_dict(),
                    "resolution_statuses": resolution_statuses,
                },
                {
                    "step": "catalog_query_retrieval",
                    "status": "completed",
                    "filter_counts": counts,
                    "matched_product_count": len(products),
                },
            ]
        )
        if not products:
            return self._finish_turn(
                state,
                message,
                None,
                trace,
                self._no_match_summary(grounded),
                "catalog_query",
                update_shopping_state=False,
                catalog_data={"kind": "catalog_query", "operations": effective_operations, "products": [], "total_count": 0, "resolution_statuses": resolution_statuses},
                guidance_products=self._products_of_type(grounded.item_type),
                guidance_kind="no_match",
            )

        scope = self._catalog_scope_label(grounded)
        summaries: list[str] = []
        data: dict[str, Any] = {
            "kind": "catalog_query",
            "operations": effective_operations,
            "total_count": len(products),
            "facets": {},
            "resolution_statuses": resolution_statuses,
        }
        if unresolved_concepts:
            summaries.append("目录中没有可验证的对应标签：" + "、".join(unresolved_concepts))
        if ambiguous_statuses:
            choices = "；".join(
                f"{status['raw_value']} 可对应 " + "、".join(status["canonical_values"])
                for status in ambiguous_statuses
            )
            summaries.append("该表达存在多个目录对应值，未将它们同时作为筛选条件：" + choices)
        if "count" in effective_operations:
            summaries.append(f"当前本地商品库中{scope}共有 {len(products)} 件商品")
        facet_specs = {
            "group_by_item_type": ("item_type", "商品类型", lambda product: product.item_type),
            "group_by_manufacturer": ("manufacturer", "厂商", lambda product: product.manufacturer),
            "group_by_tag": ("tag", "风格/标签", None),
        }
        for operation, (field, label, getter) in facet_specs.items():
            if operation not in effective_operations:
                continue
            values: dict[str, int] = {}
            if getter is None:
                for product in products:
                    for tag in product.tags:
                        values[tag] = values.get(tag, 0) + 1
            else:
                for product in products:
                    value = getter(product)
                    values[value] = values.get(value, 0) + 1
            ordered = dict(sorted(values.items(), key=lambda pair: (-pair[1], pair[0])))
            data["facets"][field] = ordered
            if field == "item_type":
                data["type_counts"] = ordered
            preview = "、".join(f"{value}（{count} 件）" for value, count in list(ordered.items())[:12])
            suffix = "等" if len(ordered) > 12 else ""
            summaries.append(f"{label}包括 {preview}{suffix}" if preview else f"未发现可用{label}")
        if "price_range" in effective_operations:
            lowest = min(products, key=lambda product: product.price)
            highest = max(products, key=lambda product: product.price)
            data["price_range"] = {"lowest": lowest.to_dict(), "highest": highest.to_dict()}
            data["lowest"] = lowest.to_dict()
            data["highest"] = highest.to_dict()
            if len(effective_operations) == 1:
                data["kind"] = "price_range"
            summaries.append(f"价格范围为 ${lowest.price:.2f} 至 ${highest.price:.2f}")
        if "price_extreme" in effective_operations:
            most_expensive = bool(re.search(r"最贵|最高|贵的|most expensive|highest", message.casefold()))
            selected = max(products, key=lambda product: product.price) if most_expensive else min(products, key=lambda product: product.price)
            data["price_extreme"] = selected.to_dict()
            data["products"] = [selected.to_dict()]
            if len(effective_operations) == 1:
                data["kind"] = "price_extreme"
            summaries.append(f"{'最贵' if most_expensive else '最便宜'}的是 {selected.name}（{selected.product_id}），价格 ${selected.price:.2f}")
        if "list" in effective_operations:
            visible = products[:5]
            data["products"] = [product.to_dict() for product in visible]
            summaries.append(f"以下展示价格较低的前 {len(visible)} 件")
            if len(effective_operations) == 1:
                data["kind"] = "product_list"
        if set(effective_operations).issubset({"count", "group_by_item_type"}):
            data["kind"] = "catalog_overview"
        summary = "；".join(summaries) + "。"
        return self._finish_turn(
            state,
            message,
            None,
            trace,
            summary,
            "catalog_query",
            update_shopping_state=False,
            catalog_data=data,
            guidance_products=products,
        )

    def _handle_product_detail(
        self, state: ConversationState, message: str, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        product_ids = self._product_ids_in_message(message)
        if not product_ids and re.search(r"这[件个款]|它|this (?:one|product)|it", message.casefold()):
            last_product_id = self._last_recommended_product_id(state)
            if last_product_id:
                product_ids = [last_product_id]
        if not product_ids:
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "product_detail_lookup", "status": "clarification_required"}],
                "请提供要查看的商品 ID，例如 P0005；也可以在推荐结果后直接问“这件商品的描述是什么？”。",
                "product_detail",
                update_shopping_state=False,
            )
        product = self.repository.by_id.get(product_ids[0])
        if product is None:
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "product_detail_lookup", "status": "not_found", "product_id": product_ids[0]}],
                f"本地商品库中没有找到商品 {product_ids[0]}。",
                "product_detail",
                update_shopping_state=False,
            )
        trace.append({"step": "product_detail_lookup", "status": "completed", "product_id": product.product_id})
        return self._finish_turn(
            state,
            message,
            None,
            trace,
            f"{product.name}（{product.product_id}）售价 ${product.price:.2f}，厂商为 {product.manufacturer}。"
            f"标签：{'、'.join(product.tags) or '无'}。商品描述：{product.description or '暂无描述'}",
            "product_detail",
            update_shopping_state=False,
            catalog_data={"kind": "product_detail", "products": [product.to_dict()]},
        )

    def _handle_product_comparison(
        self, state: ConversationState, message: str, trace: list[dict[str, Any]]
    ) -> dict[str, Any]:
        product_ids = self._product_ids_in_message(message)
        if len(product_ids) < 2:
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "product_comparison", "status": "clarification_required"}],
                "请提供至少两个商品 ID 以便比较，例如“比较 P0005 和 P0012”。",
                "product_comparison",
                update_shopping_state=False,
            )
        products = [self.repository.by_id[product_id] for product_id in product_ids if product_id in self.repository.by_id]
        missing = [product_id for product_id in product_ids if product_id not in self.repository.by_id]
        if missing:
            return self._finish_turn(
                state,
                message,
                None,
                trace + [{"step": "product_comparison", "status": "not_found", "missing_product_ids": missing}],
                "本地商品库中没有找到：" + "、".join(missing) + "。请检查商品 ID 后重试。",
                "product_comparison",
                update_shopping_state=False,
            )
        trace.append({"step": "product_comparison", "status": "completed", "product_ids": product_ids})
        price_text = "；".join(f"{product.name}（{product.product_id}）：${product.price:.2f}" for product in products)
        return self._finish_turn(
            state,
            message,
            None,
            trace,
            "已按价格、厂商、标签和描述并列展示这些商品：" + price_text,
            "product_comparison",
            update_shopping_state=False,
            catalog_data={"kind": "product_comparison", "products": [product.to_dict() for product in products]},
        )

    @staticmethod
    def _enforce_catalog_query_filters(filters: ShoppingRequirement) -> None:
        """A catalog question restricts facts; stated filters cannot be treated as preferences."""
        if filters.item_type.raw_value:
            filters.item_type.constraint_strength = "hard"
        if filters.manufacturer.raw_value:
            filters.manufacturer.constraint_strength = "hard"
        for concept in filters.concepts:
            concept.constraint_strength = "hard"

    @staticmethod
    def _catalog_resolution_statuses(grounded: GroundedRequirement) -> list[dict[str, Any]]:
        """Expose whether each catalog concept was resolved, unresolved, or ambiguous."""
        statuses: list[dict[str, Any]] = []
        for mapping in grounded.mappings:
            if mapping.get("field") != "concept":
                continue
            values = list(mapping.get("canonical_values") or [])
            status = "resolved" if len(values) == 1 else "ambiguous" if len(values) > 1 else "unresolved"
            statuses.append(
                {
                    "field": "concept",
                    "raw_value": mapping.get("raw_value", ""),
                    "status": status,
                    "canonical_values": values,
                }
            )
        return statuses

    @staticmethod
    def _product_ids_in_message(message: str) -> list[str]:
        # Use lookahead/lookbehind to handle Chinese characters adjacent to product IDs
        # \b doesn't work with non-ASCII characters, so we match P followed by 4 digits
        # and ensure it's not part of a longer alphanumeric sequence
        return _deduplicate(
            match.upper()
            for match in re.findall(r"(?<![A-Za-z0-9])P\d{4}(?![A-Za-z0-9])", message, flags=re.IGNORECASE)
        )

    @staticmethod
    def _catalog_scope_label(requirement: GroundedRequirement) -> str:
        parts: list[str] = []
        if requirement.item_type:
            parts.append(requirement.item_type)
        if requirement.hard_manufacturer:
            parts.append(requirement.hard_manufacturer)
        tag_groups = requirement.required_tag_groups or [[tag] for tag in requirement.required_tags]
        if tag_groups:
            parts.append(
                "标签为 " + "且".join("（" + "或".join(group) + "）" for group in tag_groups)
            )
        return "符合“" + "、".join(parts) + "”条件的" if parts else ""

    @staticmethod
    def _last_recommended_product_id(state: ConversationState) -> str | None:
        for event in reversed(state.events):
            if event.event_type != "assistant_message":
                continue
            result = event.payload.get("result", {})
            if result.get("response_type") == "recommendation":
                return _optional_text(result.get("purchased_product_id"))
        return None

    def _append_requirement_updates(
        self,
        state: ConversationState,
        parsed: ShoppingRequirement,
        previous: ShoppingRequirement,
        message: str,
        trace: list[dict[str, Any]],
        *,
        replace_selection: bool = False,
    ) -> None:
        """Translate a parsed turn into explicit set/replace/remove state events."""
        changes: list[dict[str, Any]] = []

        def add(field_name: str, operation: str, value: dict[str, Any]) -> None:
            event = {"field": field_name, "operation": operation, "value": value}
            state.add_event("constraint_update", event)
            changes.append(event)

        base = previous
        if replace_selection:
            add("selection", "reset", {})
            base = ShoppingRequirement()

        if parsed.item_type.raw_value:
            operation = "replace" if base.item_type.raw_value else "set"
            add("item_type", operation, asdict(parsed.item_type))
        if parsed.manufacturer.raw_value:
            operation = "replace" if base.manufacturer.raw_value else "set"
            add("manufacturer", operation, asdict(parsed.manufacturer))
        clears_existing_price = (
            base.price_constraint.has_value()
            and self._explicitly_clears_price_constraint(message)
            and not self._price_constraint_from_instruction(message).has_value()
        )
        if clears_existing_price:
            # The direct customer instruction wins over a planner that echoes a
            # historical price constraint from the conversation context.
            add("price_constraint", "clear", {})
        elif parsed.price_constraint.has_value():
            operation = "replace" if base.price_constraint.has_value() else "set"
            add("price_constraint", operation, asdict(parsed.price_constraint))

        def concept_key(concept: Concept) -> tuple[str, ...]:
            canonical_tags, _ = self.repository._ground_concept(concept)
            if canonical_tags:
                return ("tags", *sorted(_normalize(tag) for tag in canonical_tags))
            return ("raw", _normalize(concept.raw_value))

        previous_concepts = {concept_key(concept): concept for concept in base.concepts}
        for concept in parsed.concepts:
            existing = previous_concepts.get(concept_key(concept))
            if existing is None:
                add("concept", "set", asdict(concept))
            elif (
                existing.constraint_strength == "preference"
                and concept.constraint_strength == "hard"
            ):
                # A later explicit requirement must strengthen, not be hidden by an older preference.
                add("concept", "remove", {"raw_value": existing.raw_value})
                add("concept", "set", asdict(concept))

        # A user can explicitly withdraw a previously supplied theme/brand condition.
        normalized_message = _normalize(message)
        mentioned_tags = {_normalize(tag) for tag in self.repository.tags_in_text(message)}
        if any(token in normalized_message for token in {"remove", "without", "cancel", "不要", "取消", "不需要"}):
            for concept in base.concepts:
                canonical_tags, _ = self.repository._ground_concept(concept)
                if (
                    _normalize(concept.raw_value) in normalized_message
                    or any(_normalize(tag) in mentioned_tags for tag in canonical_tags)
                ):
                    add("concept", "remove", {"raw_value": concept.raw_value})

        if changes:
            trace.append({"step": "constraint_update", "status": "completed", "changes": changes})

    @staticmethod
    def _reduce_requirement(state: ConversationState) -> ShoppingRequirement:
        """Replay constraint events.  This reducer intentionally has no side effects."""
        requirement = ShoppingRequirement()
        concepts: list[Concept] = []
        for event in state.events:
            if event.event_type != "constraint_update":
                continue
            field_name = event.payload.get("field")
            operation = event.payload.get("operation")
            value = event.payload.get("value")
            if field_name == "selection" and operation == "reset":
                requirement = ShoppingRequirement()
                concepts = []
            elif field_name == "item_type" and operation in {"set", "replace"}:
                requirement.item_type = CatalogConstraint.from_value(value)
            elif field_name == "manufacturer" and operation in {"set", "replace"}:
                requirement.manufacturer = CatalogConstraint.from_value(value)
            elif field_name == "price_constraint" and operation in {"set", "replace"}:
                requirement.price_constraint = PriceConstraint.from_value(value)
            elif field_name == "price_constraint" and operation == "clear":
                requirement.price_constraint = PriceConstraint()
            elif field_name == "concept" and operation == "set":
                concept = Concept.from_dict(value)
                if concept and _normalize(concept.raw_value) not in {
                    _normalize(item.raw_value) for item in concepts
                }:
                    concepts.append(concept)
            elif field_name == "concept" and operation == "remove":
                raw_value = _normalize(str((value or {}).get("raw_value", "")))
                concepts = [item for item in concepts if _normalize(item.raw_value) != raw_value]
        requirement.concepts = concepts
        return requirement

    @staticmethod
    def _explicitly_clears_price_constraint(message: str) -> bool:
        """Recognize a direct withdrawal of an earlier budget constraint.

        This is intentionally narrow: an explicit new amount is handled as a
        replacement above, while only a clear cancellation removes the existing
        price event from the replayed selection state.
        """
        normalized = _normalize(message)
        cancellation_phrases = (
            "不用预算限制",
            "不设预算",
            "不限预算",
            "预算不限",
            "没有预算限制",
            "无预算限制",
            "预算无所谓",
            "不限制价格",
            "no budget limit",
            "without a budget limit",
            "no price limit",
            "without a price limit",
            "any price",
        )
        return any(phrase in normalized for phrase in cancellation_phrases)

    def _finish_turn(
        self,
        state: ConversationState,
        message: str,
        product_id: str | None,
        trace: list[dict[str, Any]],
        summary: str,
        response_type: str,
        pending_question: str | None = None,
        pending_fields: list[str] | None = None,
        update_shopping_state: bool = True,
        catalog_data: dict[str, Any] | None = None,
        guidance_products: list[Product] | None = None,
        guidance_kind: str | None = None,
    ) -> dict[str, Any]:
        if update_shopping_state:
            state.pending_question = pending_question
            state.pending_fields = pending_fields or []
            state.status = "awaiting_user" if pending_question else response_type
        self._update_task_context(
            state,
            response_type,
            product_id,
            trace,
            catalog_data,
            update_shopping_state=update_shopping_state,
        )
        bare_result = self._result(message, product_id, trace, summary)
        bare_result["response_type"] = response_type
        if catalog_data is not None:
            bare_result["catalog_data"] = catalog_data
        guidance = self._build_proactive_guidance(
            state, response_type, guidance_products, guidance_kind
        )
        if guidance is not None:
            bare_result["proactive_guidance"] = guidance
            trace.append(
                {
                    "step": "proactive_guidance",
                    "status": "completed",
                    "handler": "deterministic_catalog_summary",
                    "kind": guidance["kind"],
                    "scope_product_count": guidance["scope_product_count"],
                    "example_phrase_count": len(guidance["example_phrases"]),
                }
            )
        state.add_event("assistant_message", {"result": bare_result})
        result = dict(bare_result)
        result["conversation_state"] = state.to_dict()
        return result

    @staticmethod
    def _update_task_context(
        state: ConversationState,
        response_type: str,
        product_id: str | None,
        trace: list[dict[str, Any]],
        catalog_data: dict[str, Any] | None,
        *,
        update_shopping_state: bool,
    ) -> None:
        """Advance workflow state without conflating it with product requirements."""
        context = state.task_context
        transition: dict[str, Any] | None = None
        if response_type in {"recommendation", "no_match"}:
            comparison = next(
                (item for item in reversed(trace) if item.get("step") == "candidate_comparison"),
                {},
            )
            context.active_task = "selection"
            context.selection_phase = "recommended" if response_type == "recommendation" else "no_match"
            context.selected_product_id = product_id
            context.candidate_product_ids = list(comparison.get("candidate_product_ids", []))
            transition = {
                "task": "selection",
                "phase": context.selection_phase,
                "selected_product_id": product_id,
            }
        elif response_type == "exploration" and update_shopping_state:
            products = (catalog_data or {}).get("products", [])
            context.active_task = "selection"
            context.selection_phase = "exploring"
            context.selected_product_id = None
            context.candidate_product_ids = [
                str(product.get("product_id")) for product in products if product.get("product_id")
            ]
            transition = {
                "task": "selection",
                "phase": "exploring",
                "sample_product_ids": list(context.candidate_product_ids),
            }
        elif response_type == "clarification" and update_shopping_state:
            context.active_task = "selection"
            context.selection_phase = "collecting"
            context.selected_product_id = None
            context.candidate_product_ids = []
            transition = {"task": "selection", "phase": "collecting"}
        elif response_type == "catalog_query":
            operations = list((catalog_data or {}).get("operations", []))
            context.last_information_target = "catalog"
            context.last_information_operations = operations
            if context.active_task == "none":
                context.active_task = "information"
            transition = {
                "task": "information",
                "target": "catalog",
                "operations": operations,
                # A read-only query leaves any live selection task untouched,
                # whether it is still collecting or already holding a pick.
                "selection_preserved": context.active_task == "selection",
            }
        elif response_type in {"product_detail", "product_comparison"}:
            products = (catalog_data or {}).get("products", [])
            context.last_information_target = "comparison" if response_type == "product_comparison" else "product"
            context.last_information_operations = []
            if context.active_task == "none":
                context.active_task = "information"
            transition = {
                "task": "information",
                "target": context.last_information_target,
                "product_ids": [product.get("product_id") for product in products],
            }
        elif response_type == "capability_unavailable":
            context.active_task = "action"
            context.last_action = str((catalog_data or {}).get("action") or "") or None
            transition = {"task": "action", "action": context.last_action, "phase": "unavailable"}
        if transition is not None:
            trace.append({"step": "task_state", "status": "updated", **transition})

    def _mentioned_item_types(self, message: str) -> list[str]:
        lower = message.casefold()
        known_types = sorted({product.item_type for product in self.repository.products})
        mentioned = [
            item_type
            for item_type in known_types
            if re.search(rf"\b{re.escape(item_type.casefold())}\b", lower)
        ]
        for canonical, aliases in CATALOG_ITEM_TYPE_ALIASES.items():
            if canonical in known_types and any(alias.casefold() in lower for alias in aliases):
                mentioned.append(canonical)
        negated_types = {
            canonical
            for canonical in known_types
            if self._has_negated_item_type_mention(lower, canonical)
        }
        return [item_type for item_type in _deduplicate(mentioned) if item_type not in negated_types]

    @staticmethod
    def _has_negated_item_type_mention(message: str, item_type: str) -> bool:
        """Recognize a withdrawn type in a natural-language replacement request.

        This deliberately covers only direct local patterns such as “不要杯子，改成
        shirt” and “not a mug, switch to a shirt”.  It is used solely for same-turn
        conflict detection; the model still extracts the positive replacement and
        Python still verifies the state transition before retrieval.
        """
        aliases = (item_type, *CATALOG_ITEM_TYPE_ALIASES.get(item_type, ()))
        chinese_negation = r"(?:不要|不想要|不需要|不再要|别要|别买|不买|不是)"
        english_negation = r"(?:not|no\s+longer|without|instead\s+of|don't\s+want|do\s+not\s+want)"
        for alias in aliases:
            escaped_alias = re.escape(alias.casefold())
            if re.search(rf"{chinese_negation}\s*{escaped_alias}", message):
                return True
            if re.search(
                rf"{english_negation}\s+(?:an?\s+|the\s+)?{escaped_alias}\b",
                message,
            ):
                return True
        return False

    def _is_type_agnostic_gift_request(self, message: str) -> bool:
        """Return whether a gift request needs exactly one deterministic type question."""
        lower = message.casefold()
        mentions_gift = bool(re.search(r"礼物|送礼|送人|\bgifts?\b|\bpresents?\b", lower))
        return (
            mentions_gift
            and not self._mentioned_item_types(message)
            and not self._product_ids_in_message(message)
        )

    def _canonical_item_type(self, constraint: CatalogConstraint) -> str | None:
        """Resolve a type for state comparison while retaining the original user wording."""
        allowed = self.repository.catalog()["item_types"]
        if constraint.catalog_hint:
            canonical = self.repository._exact_catalog_value(constraint.catalog_hint, allowed)
            if canonical:
                return canonical
        if constraint.raw_value:
            canonical = self.repository._exact_catalog_value(constraint.raw_value, allowed)
            if canonical:
                return canonical
            normalized_raw = _normalize(constraint.raw_value)
            for canonical, aliases in CATALOG_ITEM_TYPE_ALIASES.items():
                if canonical in allowed and normalized_raw in {
                    _normalize(alias) for alias in aliases
                }:
                    return canonical
        return None

    def _products_of_type(self, item_type: str | None) -> list[Product]:
        """All catalog rows of one verified type, or the whole catalog when unknown."""
        if not item_type:
            return list(self.repository.products)
        return [
            product for product in self.repository.products if product.item_type == item_type
        ]

    def _guidance_scope(
        self, state: ConversationState, guidance_products: list[Product] | None
    ) -> list[Product]:
        """Choose the product set the guidance describes, without touching state.

        A catalog answer guides over exactly the rows it returned.  A clarification
        or no-match turn has no useful result set, so it widens to the item type the
        user already named, or to the whole catalog when even that is unknown.
        """
        if guidance_products is not None:
            return guidance_products
        return self._products_of_type(
            self._canonical_item_type(self._reduce_requirement(state).item_type)
        )

    def _build_proactive_guidance(
        self,
        state: ConversationState,
        response_type: str,
        guidance_products: list[Product] | None,
        guidance_kind: str | None = None,
    ) -> dict[str, Any] | None:
        """Turn a verified product set into concrete next steps for the user.

        This is deterministic catalog reporting, not a second recommendation: every
        number and option below is computed from products Python already retrieved,
        so it cannot claim inventory the catalog does not have.
        """
        if response_type not in PROACTIVE_RESPONSE_TYPES:
            return None
        scope = self._guidance_scope(state, guidance_products)
        highlights = self.repository.catalog_highlights(scope)
        if not highlights["count"]:
            return None
        # A small result set is already a precise answer; guidance would only
        # repeat what the user can see.
        if highlights["count"] <= FEW_RESULTS_THRESHOLD and response_type == "recommendation":
            return None
        # A catalog query that matched nothing must not describe "this batch";
        # it needs the same relaxation wording as a failed selection.
        builder = {
            "catalog_query": self._catalog_query_guidance,
            "clarification": self._clarification_guidance,
            "exploration": self._exploration_guidance,
            "no_match": self._no_match_guidance,
            "recommendation": self._recommendation_guidance,
        }[guidance_kind or response_type]
        guidance = builder(highlights)
        guidance["scope_product_count"] = highlights["count"]
        return guidance

    @staticmethod
    def _tag_phrase(highlights: dict[str, Any], limit: int = 3) -> str:
        return "、".join(
            f"{entry['value']}（{entry['count']} 件）"
            for entry in highlights["top_tags"][:limit]
        )

    @staticmethod
    def _price_phrase(highlights: dict[str, Any]) -> str:
        return f"${highlights['price_min']:.2f} 到 ${highlights['price_max']:.2f}"

    @classmethod
    def _example_phrases(cls, highlights: dict[str, Any]) -> list[str]:
        """Concrete utterances the user can reuse, derived from verified values.

        These are phrasing examples, not clickable commands: the point is to teach
        the user how to express a constraint, so the next turn stays a natural
        language turn and the model keeps doing the understanding.
        """
        phrases: list[str] = []
        bands = highlights["price_bands"]
        if bands:
            phrases.append(f"预算 {bands[0]['high']:.0f} 以内")
        for entry in highlights["top_tags"][:2]:
            phrases.append(f"{entry['value']} 风格的")
        manufacturers = highlights["top_manufacturers"]
        if manufacturers:
            phrases.append(f"{manufacturers[0]['value']} 这个厂商的")
        return phrases

    @classmethod
    def _catalog_query_guidance(cls, highlights: dict[str, Any]) -> dict[str, Any]:
        """After a read-only answer, describe the range and how to narrow it."""
        return {
            "kind": "catalog_followup",
            "message": (
                f"这批商品价格在 {cls._price_phrase(highlights)} 之间，"
                f"最常见的风格是 {cls._tag_phrase(highlights)}。"
                "想让我按预算或风格挑一件的话，直接说就行。"
            ),
            "example_phrases": cls._example_phrases(highlights),
        }

    @classmethod
    def _clarification_guidance(cls, highlights: dict[str, Any]) -> dict[str, Any]:
        """The item type is missing; state what the catalog carries in each type."""
        types = "、".join(
            f"{entry['value']}（{entry['count']} 件）" for entry in highlights["item_types"]
        )
        return {
            "kind": "selection_scope",
            "message": (
                f"目录里共有 {highlights['count']} 件商品：{types}，"
                f"价格在 {cls._price_phrase(highlights)} 之间。"
                "告诉我想买哪一类，也可以顺便带上预算或风格。"
            ),
            "example_phrases": ["马克杯", "T恤", "预算 15 以内的马克杯"],
        }

    @classmethod
    def _no_match_guidance(cls, highlights: dict[str, Any]) -> dict[str, Any]:
        """Report which prices and themes are actually reachable after relaxing."""
        return {
            "kind": "relaxation_hint",
            "message": (
                f"这个类别下共有 {highlights['count']} 件商品，"
                f"价格从 ${highlights['price_min']:.2f} 起，"
                f"较多的风格是 {cls._tag_phrase(highlights)}。"
            ),
            "example_phrases": cls._example_phrases(highlights),
        }

    @classmethod
    def _recommendation_guidance(cls, highlights: dict[str, Any]) -> dict[str, Any]:
        """A single pick out of hundreds hides the catalog; show what else fits."""
        samples = highlights["sample_products"][1:3]
        alternatives = "；".join(
            f"{product['name']}（{product['product_id']}，${product['price']:.2f}）"
            for product in samples
        )
        message = (
            f"符合条件的一共有 {highlights['count']} 件，"
            f"价格在 {cls._price_phrase(highlights)} 之间。"
        )
        if alternatives:
            message += f"同价位附近还有：{alternatives}。"
        message += f"这批商品里较多的风格是 {cls._tag_phrase(highlights)}，可以再收窄。"
        return {
            "kind": "narrowing_hint",
            "message": message,
            "example_phrases": cls._example_phrases(highlights),
        }

    def _preprocess_intent_signals(self, message: str, state: ConversationState) -> dict[str, Any]:
        """Identify strong intent signals before LLM call to reduce ambiguity."""
        lower = message.casefold()
        product_ids = self._product_ids_in_message(message)

        # Strong signal: explicit comparison with product IDs
        has_comparison_words = bool(re.search(r"比较|对比|compare|difference|vs\.?", lower))

        # Strong signal: catalog query keywords (availability, price range, etc.)
        # Split into multiple patterns for better matching
        has_catalog_query_words = bool(
            re.search(r"有.{0,20}吗", message) or  # "有...吗？" pattern
            re.search(r"有哪些|有什么|都有", message) or
            re.search(r"价位|价格范围|多少钱|最便宜|最贵|最低|最高", message) or
            re.search(r"catalog|price range|available|cheapest|most expensive", lower)
        )

        # Strong signal: explicit transaction verbs
        has_transaction_words = bool(re.search(
            r"下单|购买|支付|取消订单|order|buy|purchase|pay|cancel",
            lower
        ))

        # Strong signal: selection/recommendation verbs
        has_selection_words = bool(re.search(
            r"想买|需要|推荐|帮我选|要买|给我找|need|want|recommend|find me",
            lower
        ))
        has_product_detail_words = bool(re.search(
            r"详情|描述|标签|介绍|什么商品|多少钱|价格|detail|description|tags?|what is|price",
            lower,
        ))

        return {
            "explicit_product_ids": product_ids,
            "has_comparison_words": has_comparison_words,
            "has_catalog_query_words": has_catalog_query_words,
            "has_transaction_words": has_transaction_words,
            "has_selection_words": has_selection_words,
            "has_product_detail_words": has_product_detail_words,
            "pending_question_continuation": bool(state.pending_question and len(message.strip()) < 50),
            "is_likely_comparison": len(product_ids) >= 2 and has_comparison_words,
            "is_likely_catalog_query": has_catalog_query_words and not has_selection_words,
            "is_likely_transaction": len(product_ids) >= 1 and has_transaction_words,
            "is_likely_product_detail": (
                len(product_ids) == 1
                and has_product_detail_words
                and not has_transaction_words
            ),
        }

    @classmethod
    def _exploration_guidance(cls, highlights: dict[str, Any]) -> dict[str, Any]:
        """Invite a free-form refinement after introducing a broad category."""
        return {
            "kind": "exploration_prompt",
            "message": (
                "我还没有把预算、主题或厂商设成筛选条件。"
                "你可以按自己在意的维度继续描述，我会只更新你明确提出的条件。"
            ),
            "example_phrases": cls._example_phrases(highlights),
        }

    def _create_turn_plan(
        self,
        message: str,
        state: ConversationState,
        previous: ShoppingRequirement,
        trace: list[dict[str, Any]],
    ) -> TurnPlan:
        """Create the one declarative plan used by the public turn execution path."""
        if self.llm is None:
            raise LLMResponseError(self._settings_error or "Model client was unavailable.", error_code="configuration")

        # Phase 1: Preprocess intent signals
        signals = self._preprocess_intent_signals(message, state)
        trace.append({
            "step": "intent_signal_detection",
            "status": "completed",
            "signals": signals,
        })

        context = {"recent_messages": self._recent_conversation_messages(state)}
        shopping_context = self._shopping_context(state, previous)
        planner_messages = turn_planner_messages(
            message,
            self.repository.catalog(),
            context,
            shopping_context,
            state.last_catalog_context,
            signals,  # Pass signals to help guide the model
        )
        try:
            raw_plan = self.llm.chat_json(planner_messages)
            try:
                plan = TurnPlan.from_dict(raw_plan)
                mismatch = self._strong_signal_plan_mismatch(plan, signals)
                evidence_status, evidence_error = self._goal_evidence_status(plan, message, state)
                # Evidence is valuable for replay and evaluation, but selection is
                # not an irreversible action.  Treat a paraphrased evidence quote
                # as an audit warning there, otherwise harmless variants such as
                # “我想买个礼物” / “我想买礼物” can fail the entire customer turn.
                # Transaction plans remain strict because their evidence is the
                # user's authorization for an external action.
                if plan.goal == "action":
                    mismatch = mismatch or evidence_error
                if mismatch:
                    trace.append(
                        {
                            "step": "intent_plan_consistency",
                            "status": "mismatch",
                            "warning": mismatch,
                        }
                    )
                    raise LLMResponseError(mismatch, error_code="invalid_model_output")
            except LLMResponseError as exc:
                if exc.error_code != "invalid_model_output":
                    raise
                trace.append(
                    {
                        "step": "turn_plan_repair",
                        "status": "requested",
                        "error_code": exc.error_code,
                    }
                )
                repaired_plan = self.llm.chat_json(
                    turn_plan_repair_messages(raw_plan, str(exc))
                )
                plan = TurnPlan.from_dict(repaired_plan)
                repaired_mismatch = self._strong_signal_plan_mismatch(plan, signals)
                evidence_status, evidence_error = self._goal_evidence_status(plan, message, state)
                if plan.goal == "action":
                    repaired_mismatch = repaired_mismatch or evidence_error
                if repaired_mismatch:
                    raise LLMResponseError(repaired_mismatch, error_code="invalid_model_output")
                trace.append({"step": "turn_plan_repair", "status": "completed"})
            else:
                evidence_status, _ = self._goal_evidence_status(plan, message, state)
            trace.append(
                {
                    "step": "intent_plan_consistency",
                    "status": "completed",
                    "strong_signal": self._strongest_intent_signal(signals),
                }
            )
            trace.append(
                {
                    "step": "goal_evidence",
                    "status": evidence_status,
                    "evidence": list(plan.goal_evidence),
                }
            )
            trace.append(
                {
                    "step": "turn_planning",
                    "status": "completed",
                    "handler": "deepseek",
                    "goal": plan.goal,
                    "target": plan.target,
                    "intent": plan.intent,
                    "catalog_operations": list(plan.catalog_operations),
                    "state_action": plan.state_action,
                    "selection_mode": plan.selection_mode,
                    "action": plan.action,
                }
            )
            return plan
        except LLMResponseError as exc:
            trace.append(
                {
                    "step": "turn_planning",
                    "status": "failed",
                    "handler": "deepseek",
                    "error_code": exc.error_code,
                    "warning": str(exc),
                }
            )
            exc.workflow_trace = list(trace)
            raise

    def _enforce_pending_price_refinement(
        self,
        plan: TurnPlan,
        message: str,
        state: ConversationState,
        previous: ShoppingRequirement,
        trace: list[dict[str, Any]],
    ) -> TurnPlan:
        """Route a high-confidence budget answer back into the active selection.

        “有没有 10 块以上、20 元以下的” contains catalog-query words, but
        when it answers the pending budget question for an existing item type it
        is a refinement, not a fresh read-only catalog task.  Python owns this
        state transition so the planner cannot silently drop a valid price slot.
        """
        resolved = self._price_constraint_from_instruction(message)
        is_pending_selection = (
            state.task_context.active_task == "selection"
            and "price_constraint" in state.pending_fields
            and bool(previous.item_type.raw_value)
        )
        if (
            not is_pending_selection
            or not resolved.has_value()
            or self._product_ids_in_message(message)
        ):
            return plan

        trace.append(
            {
                "step": "pending_price_refinement",
                "status": "enforced",
                "previous_goal": plan.goal,
                "previous_target": plan.target,
                "price_constraint": resolved.to_dict(),
            }
        )
        return TurnPlan(
            goal="selection",
            target="catalog",
            customer_reply=None,
            requirement=ShoppingRequirement(price_constraint=resolved),
            catalog_operations=[],
            state_action="merge",
            selection_mode="criteria",
            action=None,
            goal_evidence=[],
        )

    def _active_selection_price_refinement_plan(
        self,
        message: str,
        state: ConversationState,
        previous: ShoppingRequirement,
        trace: list[dict[str, Any]],
    ) -> TurnPlan | None:
        """Safely continue an explicit recommendation request after a read-only query.

        Catalog questions preserve a live selection context.  If the next turn says
        “recommend one under $20”, the current item type and theme remain explicit
        state, while the stated budget is an unambiguous hard refinement.  Routing
        this narrow shape before the model prevents a malformed planner response
        from turning a valid follow-up into a service error.
        """
        price_constraint = self._price_constraint_from_instruction(message)
        has_recommendation_request = bool(
            re.search(r"推荐|帮我选|给我找|想买|要买|recommend|find\s+me", message.casefold())
        )
        can_continue = (
            state.task_context.active_task == "selection"
            and bool(previous.item_type.raw_value)
            and price_constraint.has_value()
            and has_recommendation_request
            and not self._mentioned_item_types(message)
            and not self._product_ids_in_message(message)
        )
        if not can_continue:
            return None

        trace.append(
            {
                "step": "active_selection_price_refinement",
                "status": "enforced",
                "price_constraint": price_constraint.to_dict(),
                "preserved_item_type": previous.item_type.raw_value,
            }
        )
        return TurnPlan(
            goal="selection",
            target="catalog",
            customer_reply=None,
            requirement=ShoppingRequirement(price_constraint=price_constraint),
            catalog_operations=[],
            state_action="merge",
            selection_mode="criteria",
            action=None,
            goal_evidence=[],
        )

    @staticmethod
    def _shopping_context(
        state: ConversationState, requirement: ShoppingRequirement
    ) -> dict[str, Any]:
        """A narrow hand-off interface from shopping state to the coordinator."""
        return {
            "has_active_shopping_request": bool(
                requirement.item_type.raw_value
                or requirement.manufacturer.raw_value
                or requirement.price_constraint.has_value()
                or requirement.concepts
            ),
            "pending_shopping_question": state.pending_question,
            "pending_shopping_fields": list(state.pending_fields),
            "task_context": state.task_context.to_dict(),
        }

    @staticmethod
    def _strongest_intent_signal(signals: dict[str, Any]) -> str | None:
        """Return the unambiguous intent signal that must agree with the plan."""
        if signals.get("is_likely_comparison"):
            return "product_comparison"
        if signals.get("is_likely_transaction"):
            return "transaction"
        if signals.get("is_likely_product_detail"):
            return "product_detail"
        return None

    @classmethod
    def _strong_signal_plan_mismatch(cls, plan: TurnPlan, signals: dict[str, Any]) -> str | None:
        """Guard explicit product-ID requests against a semantically wrong model route.

        This guard deliberately covers only high-confidence shapes.  Open-ended
        recommendation and catalog utterances remain the planner's responsibility.
        """
        signal = cls._strongest_intent_signal(signals)
        if signal == "product_comparison" and (plan.goal, plan.target) != ("information", "product"):
            return "Explicit product IDs with a comparison request must route to product information."
        if signal == "product_detail" and (plan.goal, plan.target) != ("information", "product"):
            return "An explicit product-detail request must route to product information."
        if signal == "transaction" and (
            (plan.goal, plan.target) != ("action", "transaction") or not plan.action
        ):
            return "An explicit transaction request must route to the transaction capability check."
        return None

    @staticmethod
    def _goal_evidence_status(
        plan: TurnPlan, message: str, state: ConversationState
    ) -> tuple[str, str | None]:
        """Audit planner evidence without making a terse follow-up impossible.

        A supplied evidence fragment must literally occur in the latest message.
        Empty evidence remains allowed for an answer to a pending question, and is
        recorded as missing otherwise so it can be measured before being made a
        hard production requirement.
        """
        if plan.goal not in {"selection", "action"}:
            return "not_required", None
        if not plan.goal_evidence:
            return ("pending_follow_up" if state.pending_question else "missing"), None
        message_normalized = message.casefold()
        invalid = [
            evidence for evidence in plan.goal_evidence
            if evidence.casefold() not in message_normalized
        ]
        if invalid:
            return "mismatch", "Plan evidence must quote the latest user message."
        return "verified", None

    @staticmethod
    def _recent_conversation_messages(state: ConversationState, limit: int = 4) -> list[dict[str, str]]:
        """Send semantic conversation context, excluding transient operational failures."""
        messages: list[dict[str, str]] = []
        for event in state.events:
            if event.event_type == "user_message":
                messages.append({"role": "user", "content": str(event.payload.get("message", ""))})
            elif event.event_type == "assistant_message":
                result = event.payload.get("result", {})
                if result.get("response_type") == "service_error":
                    continue
                messages.append({"role": "assistant", "content": str(result.get("summary", ""))})
        return messages[-limit:]

    @staticmethod
    def _clear_generic_item_type(requirement: ShoppingRequirement) -> None:
        if _normalize(requirement.item_type.raw_value or "") in {"gift", "present", "something"}:
            requirement.item_type = CatalogConstraint()

    def _rank_candidates(
        self,
        requirement: GroundedRequirement,
        candidates: list[Product],
        trace: list[dict[str, Any]],
    ) -> tuple[PurchaseDecision, Product]:
        """Select only from tool-retrieved candidates with a stable, inspectable policy.

        This deliberately runs *after* the LLM has produced and Python has validated
        a plan.  It is not an offline substitute for a failed model request: without
        a valid plan, execution stops before catalog retrieval.
        """
        ranked = self.repository.rank_candidates(candidates, requirement)
        selected = ranked[0]
        preferred_manufacturer, preferred_tags = self.repository.preference_score(
            selected, requirement
        )
        unverified_preferences = _deduplicate(
            str(mapping.get("raw_value"))
            for mapping in requirement.mappings
            if mapping.get("field") == "concept"
            and mapping.get("constraint_strength") == "preference"
            and not mapping.get("canonical_values")
        )
        applied_preferences: list[str] = []
        if requirement.preferred_manufacturer:
            applied_preferences.append(
                "优先厂商" if preferred_manufacturer else "厂商偏好未满足"
            )
        if requirement.preferred_tags:
            applied_preferences.append(
                f"命中 {preferred_tags} 个已验证偏好标签"
            )

        ranking_policy = ["已验证偏好", "价格从低到高", "商品 ID"]
        reason = "已通过商品类型、预算、厂商和主题等硬条件校验。"
        if applied_preferences:
            reason += "在候选中按" + "、".join(applied_preferences) + "排序，再按价格和商品 ID 打破平局。"
        else:
            reason += "没有可区分候选的已验证偏好，因此按价格从低到高、商品 ID 的稳定规则排序。"
        tradeoffs: list[str] = []
        if unverified_preferences:
            tradeoffs.append(
                "以下偏好未能映射到商品库标签，未作为匹配事实："
                + "、".join(unverified_preferences)
            )
        decision = PurchaseDecision(
            purchased_product_id=selected.product_id,
            reason=reason,
            tradeoffs=tradeoffs,
            confidence="medium" if unverified_preferences else "high",
            match_level="closest_alternative" if unverified_preferences else "exact_match",
        )
        visible_candidates = ranked[: self.max_candidates]
        trace.append(
            {
                "step": "candidate_comparison",
                "status": "completed",
                "handler": "deterministic_ranking",
                "candidate_product_ids": [candidate.product_id for candidate in visible_candidates],
                "eligible_product_count": len(ranked),
                "ranking_policy": ranking_policy,
                "selected_product_id": selected.product_id,
                "selected_preference_score": {
                    "preferred_manufacturer": preferred_manufacturer,
                    "preferred_tags": preferred_tags,
                },
            }
        )
        trace.append(
            {
                "step": "decision_validation",
                "status": "accepted",
                "handler": "catalog_constraints",
                "selected_product_id": selected.product_id,
                "hard_constraints_verified": True,
                "unverified_preferences": unverified_preferences,
            }
        )
        return decision, selected

    @staticmethod
    def _exploration_summary(
        requirement: ShoppingRequirement,
        highlights: dict[str, Any],
        question: str,
    ) -> str:
        """Describe a broad catalog slice without turning it into a premature pick."""
        item_type = requirement.item_type.raw_value or "这类商品"
        top_tags = "、".join(
            f"{entry['value']}（{entry['count']} 件）"
            for entry in highlights["top_tags"][:3]
        )
        tag_sentence = f"常见主题有 {top_tags}。" if top_tags else ""
        return (
            f"我先按“{item_type}”帮你浏览了商品库：共有 {highlights['count']} 件，"
            f"价格从 ${highlights['price_min']:.2f} 到 ${highlights['price_max']:.2f}。"
            f"{tag_sentence}下面展示了几件价格较低的商品作为参考。{question}"
        )

    @staticmethod
    def _format_summary(selected: Product, decision: PurchaseDecision) -> str:
        match_note = ""
        if decision.match_level == "closest_alternative":
            match_note = "偏好条件未能完全验证，以下结果仅保证已满足的硬条件。"
        summary = (
            f"推荐购买 {selected.name}（{selected.product_id}），价格 ${selected.price:.2f}。"
            f"{match_note}{decision.reason}"
        )
        if decision.tradeoffs:
            summary += " 取舍：" + "；".join(decision.tradeoffs) + "。"
        return summary

    @staticmethod
    def _no_match_summary(
        requirement: GroundedRequirement,
        alternatives: list[dict[str, Any]] | None = None,
    ) -> str:
        if requirement.unresolved_hard_constraints:
            return "商品库无法满足以下硬性条件：" + "；".join(requirement.unresolved_hard_constraints) + "。"
        constraints = []
        if requirement.item_type:
            constraints.append(f"类型为 {requirement.item_type}")
        if requirement.hard_manufacturer:
            constraints.append(f"制造商为 {requirement.hard_manufacturer}")
        if ProductRepository._constrains(requirement, "price"):
            constraints.append(ProductRepository._price_description(requirement))
        tag_groups = requirement.required_tag_groups or [[tag] for tag in requirement.required_tags]
        if tag_groups:
            constraints.append(
                "标签包含 " + " 且 ".join("（" + " 或 ".join(group) + "）" for group in tag_groups)
            )
        detail = "、".join(constraints) or "当前条件"
        summary = f"商品库中没有同时满足{detail}的商品。"
        if not alternatives:
            return summary + "可以尝试放宽预算、品牌或主题条件。"
        return summary + ShoppingAgent._relaxation_advice(alternatives)

    @staticmethod
    def _relaxation_advice(alternatives: list[dict[str, Any]]) -> str:
        """State which single condition blocked the search, with the real near miss."""
        labels = {"price": "预算", "manufacturer": "厂商", "tags": "主题"}
        parts: list[str] = []
        for item in alternatives:
            constraint = item["relaxed_constraint"]
            closest = item["products"][0]
            gap = item["gap"]
            product = f"{closest['name']}（{closest['product_id']}，${closest['price']:.2f}）"
            if constraint == "price":
                parts.append(
                    f"若放宽价格条件（{gap['requested']}），可选 {product}"
                    f"，它满足其余全部条件"
                )
            elif constraint == "manufacturer":
                parts.append(
                    f"若不限定厂商 {gap['requested']}，可选 {product}"
                    f"，厂商为 {gap['actual']}"
                )
            else:
                parts.append(
                    f"若不限定主题，可选 {product}，其标签为 {'、'.join(gap['actual'])}"
                )
        joined = "；".join(parts)
        others = sum(item["match_count"] for item in alternatives) - len(alternatives)
        tail = f"。放宽后另有 {others} 件可选。" if others > 0 else "。"
        return f"最接近的结果是：{joined}{tail}"

    @staticmethod
    def _looks_like_missing_price(instruction: str) -> bool:
        return bool(
            re.search(
                r"\b(?:under|less than|below|at most|within)\s*\.\s*$",
                instruction.casefold(),
            )
        )

    @staticmethod
    def _resolve_price_constraint(
        instruction: str, requirement: ShoppingRequirement, trace: list[dict[str, Any]]
    ) -> None:
        """Resolve common natural-language price operators independently of model phrasing."""
        resolved = ShoppingAgent._price_constraint_from_instruction(instruction)
        if not resolved.has_value():
            return
        if requirement.price_constraint != resolved:
            trace.append(
                {
                    "step": "price_constraint_resolution",
                    "status": "completed",
                    "operator": resolved.operator,
                    "value": resolved.value,
                    "price_constraint": resolved.to_dict(),
                }
            )
            requirement.price_constraint = resolved

    @staticmethod
    def _price_constraint_from_instruction(instruction: str) -> PriceConstraint:
        """Parse high-confidence bilingual price bounds without relying on the LLM.

        The range patterns run first so “10 块以上、20 元以下” is represented
        as two bounds rather than accidentally being collapsed into one budget.
        """
        bounds = ShoppingAgent._price_range_bounds_from_instruction(instruction)
        if bounds is not None:
            minimum, maximum = bounds
            if minimum > maximum:
                return PriceConstraint()
            return PriceConstraint(
                min_value=minimum,
                max_value=maximum,
            )

        match = re.search(
            r"\b(?P<phrase>under|less than|below|at most|no more than|within)\s*\$?\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            instruction.casefold(),
        )
        if match:
            phrase = match.group("phrase")
            operator = "<" if phrase in {"under", "less than", "below"} else "<="
            return PriceConstraint(operator=operator, value=float(match.group("value")))

        chinese_match = re.search(
            r"(?P<phrase>低于|小于|不超过|不高于|最多|以下|以内|预算(?:为|是)?)\s*[$￥¥]?\s*"
            r"(?P<value>\d+(?:\.\d+)?)",
            instruction,
        )
        if chinese_match:
            phrase = chinese_match.group("phrase")
            operator = "<" if phrase in {"低于", "小于"} else "<="
            return PriceConstraint(operator=operator, value=float(chinese_match.group("value")))

        chinese_upper = re.search(
            r"(?P<value>\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?P<phrase>以下|以内)",
            instruction,
        )
        if chinese_upper:
            return PriceConstraint(
                operator="<=",
                value=float(chinese_upper.group("value")),
            )

        chinese_lower = re.search(
            r"(?P<value>\d+(?:\.\d+)?)\s*(?:元|块)?\s*(?P<phrase>以上|不少于|不低于|至少)",
            instruction,
        )
        if chinese_lower:
            return PriceConstraint(
                operator=">=",
                value=float(chinese_lower.group("value")),
            )
        return PriceConstraint()

    @staticmethod
    def _price_range_bounds_from_instruction(instruction: str) -> tuple[float, float] | None:
        """Extract two stated price bounds without deciding whether their order is valid."""
        number = r"\d+(?:\.\d+)?"
        currency = r"(?:[$￥¥]|元|块|rmb|yuan|dollars?)?"
        patterns = (
            rf"(?P<min>{number})\s*{currency}\s*(?:及)?以上\s*(?:到|至|[-~—,，、])?\s*"
            rf"(?P<max>{number})\s*{currency}\s*(?:及)?以下",
            rf"\b(?:between|from)\s*[$￥¥]?\s*(?P<min>{number})\s*"
            rf"(?:and|to|-)\s*[$￥¥]?\s*(?P<max>{number})\b",
            rf"(?P<min>{number})\s*{currency}\s*(?:到|至|[-~—])\s*"
            rf"(?P<max>{number})\s*{currency}",
        )
        for pattern in patterns:
            match = re.search(pattern, instruction, flags=re.IGNORECASE)
            if match:
                return float(match.group("min")), float(match.group("max"))
        return None

    @staticmethod
    def _looks_like_inverted_price_range(instruction: str) -> bool:
        bounds = ShoppingAgent._price_range_bounds_from_instruction(instruction)
        return bool(bounds and bounds[0] > bounds[1])

    def _enforce_primary_topic_constraints(
        self, instruction: str, requirement: ShoppingRequirement, trace: list[dict[str, Any]]
    ) -> None:
        """Promote concepts in the requested item's main clause, not later preference clauses."""
        primary_text = re.split(
            r"\bprefer(?:red)?\b|喜欢|偏好|优先", instruction, maxsplit=1, flags=re.IGNORECASE
        )[0]
        normalized_primary = _normalize(primary_text)
        primary_tags = self.repository.tags_in_text(primary_text)
        promoted: list[str] = []
        existing_hints = {
            hint.casefold()
            for concept in requirement.concepts
            for hint in concept.catalog_tag_hints
        }
        for tag in primary_tags:
            matched_concept = next(
                (
                    concept
                    for concept in requirement.concepts
                    if tag.casefold() in {hint.casefold() for hint in concept.catalog_tag_hints}
                ),
                None,
            )
            if matched_concept is not None:
                if matched_concept.constraint_strength != "hard":
                    matched_concept.constraint_strength = "hard"
                    promoted.append(tag)
            elif tag.casefold() not in existing_hints:
                requirement.concepts.append(
                    Concept(
                        raw_value=tag,
                        kind="theme",
                        constraint_strength="hard",
                        catalog_tag_hints=[tag],
                    )
                )
                promoted.append(tag)
        # A model can map a Chinese phrase to an exact English tag outside our small
        # handwritten alias table (for example, "纽约风" -> "New York").  Its role is
        # determined by its position in the request, not by whether that translation
        # happened to be listed in the alias table.
        for concept in requirement.concepts:
            raw_value = _normalize(concept.raw_value)
            if (
                raw_value
                and raw_value in normalized_primary
                and concept.catalog_tag_hints
                and concept.constraint_strength != "hard"
            ):
                concept.constraint_strength = "hard"
                promoted.append(concept.raw_value)
        if promoted:
            trace.append(
                {
                    "step": "primary_topic_enforcement",
                    "status": "completed",
                    "promoted_to_hard_tags": promoted,
                }
            )

    @staticmethod
    def _result(
        instruction: str, product_id: str | None, trace: list[dict[str, Any]], summary: str
    ) -> dict[str, Any]:
        return {
            "instruction": instruction,
            "purchased_product_id": product_id,
            "trace": trace,
            "summary": summary,
        }


# ===== Task interface =====

class Agent(ShoppingAgent):
    """Public task interface: `Agent(data_dir).run(instruction)`."""

    pass

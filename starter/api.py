"""FastAPI wrapper around the shopping Agent.

Start with::

    uvicorn starter.api:app --reload

Endpoints
---------
GET  /health                     — liveness + model observability snapshot
POST /api/conversations          — create a new conversation
GET  /api/conversations/{id}     — get conversation state
POST /api/conversations/{id}/messages  — send a message, get full turn result
GET  /api/products               — search / list products (paginated)
GET  /api/products/{id}          — get single product detail
GET  /api/catalog/facets         — browse catalog facets
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

import secrets

from fastapi import APIRouter, Depends, FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from starter import auth, catalog, store
from starter.agent_interface import Agent, ConversationState
from starter.v3_engine import ExecutionContext

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"

_PRIVATE_TRACE_FIELDS = {
    "warning",
    "raw_response",
    "prompt",
    "messages",
    "api_key",
    "authorization",
}


def _sanitize_public(value: Any) -> Any:
    """Recursively remove provider text and credential-shaped fields."""
    if isinstance(value, dict):
        return {
            key: _sanitize_public(item)
            for key, item in value.items()
            if key.casefold() not in _PRIVATE_TRACE_FIELDS
        }
    if isinstance(value, list):
        return [_sanitize_public(item) for item in value]
    return value


def _public_trace(trace: Any) -> list[dict[str, Any]]:
    if not isinstance(trace, list):
        return []
    return [_sanitize_public(step) for step in trace if isinstance(step, dict)]


def _state_for_persistence(state: ConversationState) -> dict[str, Any]:
    """Persist only recursively sanitized state and result traces."""
    return _sanitize_public(state.to_dict())

def _extract_alternatives(catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """Pull closest-alternative products from catalog_data."""
    alternatives = catalog.get("alternatives", [])
    return [
        {
            "relaxed_constraint": alt.get("relaxed_constraint", ""),
            "products": alt.get("products", [])[:1],
        }
        for alt in alternatives
    ]


def _extract_products(result: dict[str, Any]) -> list[dict[str, Any]]:
    """Return product dicts for a turn result.

    Recommendation turns don't carry ``catalog_data.products``; their candidate
    product IDs live in the ``candidate_comparison`` trace step instead.  Resolve
    those IDs back to full product dicts so the UI can render cards.
    """
    catalog = result.get("catalog_data") or {}
    products = catalog.get("products", [])
    if products:
        return products

    # Bundle recommendations carry products under catalog_data already.
    if result.get("response_type") == "bundle_recommendation":
        return catalog.get("products", [])

    # Recommendation: resolve candidate_product_ids from the comparison trace.
    if result.get("response_type") == "recommendation":
        for step in reversed(result.get("trace", [])):
            if step.get("step") == "candidate_comparison":
                ids = step.get("candidate_product_ids", [])
                resolved = [_agent.repository.by_id[pid].to_dict() for pid in ids if pid in _agent.repository.by_id]
                return resolved
    return []


router = APIRouter()

# ── shared agent instance ──────────────────────────────────────────────
_agent = Agent(DATA_DIR)
_conversation_locks: dict[str, asyncio.Lock] = {}
_conversation_locks_guard = asyncio.Lock()


async def _conversation_lock(conversation_id: str) -> asyncio.Lock:
    """Return the process-local lock used to serialize one conversation turn."""
    async with _conversation_locks_guard:
        return _conversation_locks.setdefault(conversation_id, asyncio.Lock())


# ── request / response models ──────────────────────────────────────────

class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, max_length=4000, description="User message text")
    client_message_id: str | None = Field(default=None, min_length=8, max_length=80)


class ConversationTitleRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=80)


class TurnResponse(BaseModel):
    conversation_id: str
    response_type: str
    summary: str
    purchased_product_id: str | None = None
    trace: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    guidance: dict[str, Any] | None = None
    bundle: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    request_id: str | None = None


class RegisterRequest(BaseModel):
    email: str = Field(..., description="Email address")
    password: str = Field(..., min_length=6, description="Password (min 6 chars)")
    name: str | None = None


class LoginRequest(BaseModel):
    email: str
    password: str


class UserResponse(BaseModel):
    id: str
    email: str
    name: str | None = None
    role: str = "user"


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user: UserResponse


# ── auth helpers ───────────────────────────────────────────────────────

_bearer = HTTPBearer(auto_error=False)


def get_current_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> UserResponse:
    if credentials is None:
        raise HTTPException(status_code=401, detail="未提供认证令牌")
    try:
        user_id = auth.decode_access_token(credentials.credentials)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    user = auth.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return UserResponse(**user)


def require_admin(user: UserResponse = Depends(get_current_user)) -> UserResponse:
    if user.role != "admin":
        raise HTTPException(status_code=403, detail="需要管理员权限")
    return user


def optional_user(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> UserResponse | None:
    """Return a guest only when no token was supplied; reject invalid tokens."""
    if credentials is None:
        return None
    try:
        user_id = auth.decode_access_token(credentials.credentials)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    user = auth.get_user_by_id(user_id)
    if user is None:
        raise HTTPException(status_code=401, detail="用户不存在")
    return UserResponse(**user)


# ── auth endpoints ─────────────────────────────────────────────────────

@router.post("/auth/register", response_model=TokenResponse)
async def register(body: RegisterRequest) -> TokenResponse:
    try:
        user = auth.create_user(body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    token = auth.create_access_token(user["id"])
    return TokenResponse(access_token=token, user=UserResponse(**user))


@router.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    try:
        user = auth.authenticate_user(body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    token = auth.create_access_token(user["id"])
    return TokenResponse(access_token=token, user=UserResponse(**user))


@router.get("/auth/me", response_model=UserResponse)
async def me(user: UserResponse = Depends(get_current_user)) -> UserResponse:
    return user


# ── cart endpoints ─────────────────────────────────────────────────────

class CartItemRequest(BaseModel):
    product_id: str
    quantity: int = Field(default=1, ge=1, le=99)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1, le=99)


def _enrich_cart(cart: dict) -> dict:
    """Attach product details to cart line items."""
    items = []
    total = 0.0
    for item in cart["items"]:
        product = _agent.repository.by_id.get(item["product_id"])
        if product is None:
            continue
        total += product.price * item["quantity"]
        items.append({**item, "product": product.to_dict()})
    return {"cart_id": cart["cart_id"], "items": items, "total_price": round(total, 2)}


@router.get("/cart")
async def get_cart(user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.get_cart(user.id))


@router.post("/cart/items")
async def add_cart_item(body: CartItemRequest, user: UserResponse = Depends(get_current_user)) -> dict:
    if body.product_id not in _agent.repository.by_id:
        raise HTTPException(status_code=404, detail="商品不存在")
    return _enrich_cart(store.add_cart_item(user.id, body.product_id, body.quantity))


@router.patch("/cart/items/{item_id}")
async def update_cart_item(item_id: str, body: CartItemUpdate, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_cart(store.update_cart_item(user.id, item_id, body.quantity))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.delete("/cart/items/{item_id}")
async def remove_cart_item(item_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.remove_cart_item(user.id, item_id))


@router.delete("/cart")
async def clear_cart(user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.clear_cart(user.id))


# ── favorites endpoints ────────────────────────────────────────────────

class FavoriteRequest(BaseModel):
    product_id: str


def _enrich_favorites(favorites: list[dict]) -> list[dict]:
    """为收藏项关联商品明细（商品不存在时跳过）。"""
    enriched = []
    for fav in favorites:
        product = _agent.repository.by_id.get(fav["product_id"])
        if product is None:
            continue
        enriched.append({**fav, "product": product.to_dict()})
    return enriched


@router.post("/favorites")
async def add_favorite(body: FavoriteRequest, user: UserResponse = Depends(get_current_user)) -> dict:
    if body.product_id not in _agent.repository.by_id:
        raise HTTPException(status_code=404, detail="商品不存在")
    store.add_favorite(user.id, body.product_id)
    return {"favorites": _enrich_favorites(store.list_favorites(user.id))}


@router.get("/favorites")
async def list_favorites(user: UserResponse = Depends(get_current_user)) -> dict:
    return {"favorites": _enrich_favorites(store.list_favorites(user.id))}


@router.delete("/favorites/{product_id}")
async def remove_favorite(product_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    store.remove_favorite(user.id, product_id)
    return {"favorites": _enrich_favorites(store.list_favorites(user.id))}


# ── order endpoints ────────────────────────────────────────────────────

class OrderCreateRequest(BaseModel):
    cart_id: str | None = None


def _enrich_order(order: dict) -> dict:
    """Attach product details to order line items."""
    items = []
    for item in order["items"]:
        product = _agent.repository.by_id.get(item["product_id"])
        items.append({**item, "product": product.to_dict() if product else None})
    return {**order, "items": items}


@router.post("/orders")
async def create_order(
    body: OrderCreateRequest,
    user: UserResponse = Depends(get_current_user),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> dict:
    if idempotency_key is not None and not (8 <= len(idempotency_key) <= 80):
        raise HTTPException(status_code=400, detail="Idempotency-Key 长度需在 8-80 之间")
    prices = {product.product_id: product.price for product in _agent.repository.products}
    try:
        order = store.create_order_from_cart(user.id, prices, idempotency_key=idempotency_key)
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    return _enrich_order(order)


@router.get("/orders")
async def list_orders(user: UserResponse = Depends(get_current_user)) -> list[dict]:
    return [_enrich_order(o) for o in store.list_orders(user.id)]


@router.get("/orders/{order_id}")
async def get_order(order_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_order(store.get_order(user.id, order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@router.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_order(store.cancel_order(user.id, order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# ── admin endpoints ────────────────────────────────────────────────────

@router.get("/admin/orders")
async def admin_orders(_: UserResponse = Depends(require_admin)) -> list[dict]:
    return [_enrich_order(o) for o in store.list_all_orders()]


@router.post("/admin/orders/{order_id}/ship")
async def admin_ship(order_id: str, _: UserResponse = Depends(require_admin)) -> dict:
    try:
        return _enrich_order(store.admin_ship_order(order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.post("/admin/orders/{order_id}/deliver")
async def admin_deliver(order_id: str, _: UserResponse = Depends(require_admin)) -> dict:
    try:
        return _enrich_order(store.admin_deliver_order(order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@router.get("/admin/users")
async def admin_users(_: UserResponse = Depends(require_admin)) -> list[dict]:
    return auth.list_users()


# ── admin product management ───────────────────────────────────────────

class ProductUpsert(BaseModel):
    name: str = Field(..., min_length=1, max_length=160)
    item_type: Literal["mug", "shirt"]
    manufacturer: str = Field(..., min_length=1, max_length=120)
    price: float = Field(..., gt=0)
    tags: list[str] = Field(default_factory=list, max_length=30)
    description: str = Field(default="", max_length=1000)


def _reload_agent() -> None:
    """商品目录写回后刷新内存 repository。"""
    global _agent
    _agent = Agent(DATA_DIR)


@router.post("/admin/products")
async def admin_create_product(body: ProductUpsert, _: UserResponse = Depends(require_admin)) -> dict:
    try:
        product = catalog.create_product(body.model_dump())
    except (catalog.CatalogError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _reload_agent()
    return product


@router.patch("/admin/products/{product_id}")
async def admin_update_product(product_id: str, body: ProductUpsert, _: UserResponse = Depends(require_admin)) -> dict:
    try:
        product = catalog.update_product(product_id, body.model_dump())
    except (catalog.CatalogError, ValueError) as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    _reload_agent()
    return product


@router.delete("/admin/products/{product_id}")
async def admin_delete_product(product_id: str, _: UserResponse = Depends(require_admin)) -> dict:
    try:
        catalog.delete_product(product_id)
    except catalog.CatalogError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None
    _reload_agent()
    return {"deleted": product_id}


# ── health ─────────────────────────────────────────────────────────────

@router.get("/health")
async def health() -> dict[str, Any]:
    snapshot = _agent.observability_snapshot()
    return {
        "status": "ok",
        "model": snapshot,
        "catalog_products": len(_agent.repository.products),
    }


# ── conversations ──────────────────────────────────────────────────────

@router.get("/api/conversations")
async def list_conversations(user: UserResponse = Depends(get_current_user)) -> list[dict]:
    return store.list_user_conversations(user.id)


@router.post("/api/conversations")
async def create_conversation(user: UserResponse | None = Depends(optional_user)) -> dict[str, str]:
    state = ConversationState()
    guest_token = secrets.token_urlsafe(32) if user is None else None
    store.save_conversation(
        state.conversation_id,
        user.id if user else None,
        json.dumps(state.to_dict(), ensure_ascii=False),
        guest_token=guest_token,
    )
    response = {"conversation_id": state.conversation_id}
    if guest_token is not None:
        response["conversation_access_token"] = guest_token
    return response


def _authorized_conversation(
    conversation_id: str,
    user: UserResponse | None,
    guest_token: str | None,
) -> dict[str, Any]:
    """Load a conversation and enforce owner or guest-token access."""
    record = store.load_conversation_record(conversation_id)
    if record is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    owner_id = record["user_id"]
    if owner_id:
        if user is None:
            raise HTTPException(status_code=401, detail="登录后才能访问该会话")
        if user.id != owner_id:
            raise HTTPException(status_code=403, detail="无权访问该会话")
    else:
        if not guest_token:
            raise HTTPException(status_code=401, detail="缺少游客会话令牌")
        if not store.verify_guest_conversation_token(record, guest_token):
            raise HTTPException(status_code=403, detail="游客会话令牌无效")
    return record


@router.get("/api/conversations/{conversation_id}")
async def get_conversation(
    conversation_id: str,
    user: UserResponse | None = Depends(optional_user),
    guest_token: str | None = Header(default=None, alias="X-Conversation-Token"),
) -> dict[str, Any]:
    state = _authorized_conversation(conversation_id, user, guest_token)["state"]
    # Persisted assistant events predate the HTTP response envelope. Hydrate
    # cards here so history restoration has the same data as a live turn.
    for event in state.get("events", []):
        result = (event.get("payload") or {}).get("result") if isinstance(event, dict) else None
        if not isinstance(result, dict):
            continue
        result["trace"] = _public_trace(result.get("trace"))
        catalog_data = result.setdefault("catalog_data", {})
        if isinstance(catalog_data, dict) and not catalog_data.get("products"):
            catalog_data["products"] = _extract_products(result)
    return state


def _conversation_title_from_message(message: str) -> str | None:
    """Create a stable, compact default title from the first real user input."""
    normalized = " ".join(message.split())
    if not normalized:
        return None
    return normalized[:40] + ("…" if len(normalized) > 40 else "")


@router.patch("/api/conversations/{conversation_id}/title")
async def rename_conversation(
    conversation_id: str,
    body: ConversationTitleRequest,
    user: UserResponse = Depends(get_current_user),
) -> dict[str, str]:
    title = _conversation_title_from_message(body.title)
    if title is None or not store.rename_conversation(conversation_id, user.id, title):
        raise HTTPException(status_code=404, detail="Conversation not found")
    return {"conversation_id": conversation_id, "title": title}


@router.post("/api/conversations/{conversation_id}/messages")
async def send_message(
    conversation_id: str,
    body: MessageRequest,
    user: UserResponse | None = Depends(optional_user),
    guest_token: str | None = Header(default=None, alias="X-Conversation-Token"),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
) -> TurnResponse:
    if body.client_message_id and idempotency_key and body.client_message_id != idempotency_key:
        raise HTTPException(status_code=400, detail="请求体与请求头中的幂等键不一致")
    if idempotency_key is not None and not (8 <= len(idempotency_key) <= 80):
        raise HTTPException(status_code=400, detail="Idempotency-Key 长度需在 8-80 之间")
    message_id = body.client_message_id or idempotency_key or secrets.token_urlsafe(18)
    lock = await _conversation_lock(conversation_id)
    async with lock:
        record = _authorized_conversation(conversation_id, user, guest_token)
        cached = store.load_processed_turn(conversation_id, message_id)
        if cached is not None:
            return TurnResponse.model_validate(cached)
        state = ConversationState.from_dict(record["state"])
        account_user_id = record["user_id"]
        # The model client is synchronous. Moving it off the event loop keeps
        # health checks and other conversations responsive during a long turn.
        execution_context = ExecutionContext(
            user_id=account_user_id,
            role=user.role if (user and account_user_id == user.id) else "guest",
            message_id=message_id,
            expected_revision=record["revision"],
        )

        def store_query(kind: str, user_id: str | None) -> dict[str, Any] | None:
            """Read the logged-in user's account state for the dialogue read clauses."""
            if user_id is None:
                return None
            if kind == "order_query":
                return {"orders": [_enrich_order(o) for o in store.list_orders(user_id)]}
            if kind == "favorite_list":
                return {"favorites": _enrich_favorites(store.list_favorites(user_id))}
            if kind == "cart_query":
                return {"cart": _enrich_cart(store.get_cart(user_id))}
            return None

        result = await run_in_threadpool(
            _agent.run_turn, body.message, state, execution_context, store_query
        )
        effects = list(result.pop("effects", []) or [])
        title = _conversation_title_from_message(body.message) if record["user_id"] and not record.get("title") else None
        catalog = result.get("catalog_data") or {}
        error = None
        if result.get("response_type") == "service_error":
            failed = next((step for step in reversed(result.get("trace", [])) if step.get("step") == "model_service"), {})
            code = failed.get("error_code", "model_unavailable")
            error = {
                "code": code,
                "retriable": code in {
                    "timeout", "connection", "rate_limit", "provider_status",
                    "model_request_error", "circuit_open",
                },
            }
        response = TurnResponse(
            conversation_id=conversation_id,
            response_type=result.get("response_type", "unknown"),
            summary=result.get("summary", ""),
            purchased_product_id=result.get("purchased_product_id"),
            trace=_public_trace(result.get("trace")),
            products=_extract_products(result),
            alternatives=_extract_alternatives(catalog),
            guidance=result.get("proactive_guidance"),
            bundle=catalog.get("bundle"),
            error=error,
            request_id=message_id,
        )
        # Retriable model/protocol failures are deliberately not persisted or
        # registered as processed messages. Reusing the same request ID then
        # performs a real retry while the last valid conversation stays intact.
        if result.get("response_type") == "service_error":
            return response
        try:
            commit_status = store.commit_conversation_turn(
                conversation_id=conversation_id,
                expected_revision=record["revision"],
                state_json=json.dumps(_state_for_persistence(state), ensure_ascii=False),
                response_json=response.model_dump_json(),
                message_id=message_id,
                user_id=account_user_id,
                effects=effects,
                title=title,
            )
        except store.StoreError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from None
        if commit_status == "replayed":
            replayed = store.load_processed_turn(conversation_id, message_id)
            if replayed is not None:
                return TurnResponse.model_validate(replayed)
        if commit_status != "committed":
            raise HTTPException(status_code=409, detail="会话已在其他窗口更新，请刷新后重试")
        return response


# ── products ───────────────────────────────────────────────────────────

@router.get("/api/products")
async def list_products(
    q: str = Query(default="", description="Search keyword"),
    item_type: str = Query(default="", description="Product type filter"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=12, ge=1, le=100),
) -> dict[str, Any]:
    products = [
        p for p in _agent.repository.products
        if (not item_type or p.item_type == item_type)
        and (not q or q.casefold() in f"{p.name} {p.manufacturer} {p.description} {' '.join(p.tags)}".casefold())
    ]
    products.sort(key=lambda p: (p.price, p.name))
    total = len(products)
    start = (page - 1) * page_size
    return {
        "total": total,
        "page": page,
        "page_size": page_size,
        "products": [p.to_dict() for p in products[start : start + page_size]],
    }


@router.get("/api/products/{product_id}")
async def get_product(product_id: str) -> dict[str, Any]:
    product = _agent.repository.by_id.get(product_id.upper())
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product.to_dict()


# ── catalog ────────────────────────────────────────────────────────────

@router.get("/api/catalog/facets")
async def catalog_facets() -> dict[str, Any]:
    catalog = _agent.repository.catalog()
    return {
        "item_types": catalog["item_types"],
        "manufacturers": catalog["manufacturers"],
        "tags": catalog["tags"],
        "available_fields": _agent.repository.available_fields,
        "total_products": len(_agent.repository.products),
    }


def create_app(
    *,
    data_dir: str | Path = DATA_DIR,
    local_state_dir: str | Path | None = None,
    agent: Agent | None = None,
) -> FastAPI:
    """Build the API with injectable state for isolated HTTP and E2E tests.

    The production module still exports ``app`` below.  Tests can provide an
    isolated directory and deterministic Agent without touching user data.
    """
    global DATA_DIR, _agent
    DATA_DIR = Path(data_dir)
    if local_state_dir is not None:
        state_dir = Path(local_state_dir)
        auth.configure_db_path(state_dir / "users.db")
        store.configure_db_path(state_dir / "store.db")
    _agent = agent or Agent(DATA_DIR, local_state_dir=local_state_dir)

    application = FastAPI(title="Shopping Agent API", version="0.3.0")
    cors_origins = [origin.strip() for origin in os.getenv("CORS_ORIGINS", "http://localhost:5173").split(",") if origin.strip()]
    application.add_middleware(
        CORSMiddleware,
        allow_origins=cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    images_dir = DATA_DIR / "images"
    if images_dir.is_dir():
        application.mount("/images", StaticFiles(directory=str(images_dir)), name="images")
    avatars_dir = DATA_DIR / "avatars"
    if avatars_dir.is_dir():
        application.mount("/avatars", StaticFiles(directory=str(avatars_dir)), name="avatars")
    application.include_router(router)
    return application


app = create_app()

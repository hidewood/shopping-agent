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

from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from starter import auth, store
from starter.agent_interface import Agent, ConversationState

PROJECT_DIR = Path(__file__).resolve().parents[1]
DATA_DIR = PROJECT_DIR / "data"

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


app = FastAPI(title="Shopping Agent API", version="0.1.0")

# Static product images (data/images/<productImg>.jpg)
_images_dir = DATA_DIR / "images"
if _images_dir.is_dir():
    app.mount("/images", StaticFiles(directory=str(_images_dir)), name="images")

# Static avatars (data/avatars/*.png)
_avatars_dir = DATA_DIR / "avatars"
if _avatars_dir.is_dir():
    app.mount("/avatars", StaticFiles(directory=str(_avatars_dir)), name="avatars")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── shared agent instance ──────────────────────────────────────────────
_agent = Agent(DATA_DIR)
# In-memory conversation store (replace with a database for production).
_conversations: dict[str, ConversationState] = {}


# ── request / response models ──────────────────────────────────────────

class MessageRequest(BaseModel):
    message: str = Field(..., min_length=1, description="User message text")


class TurnResponse(BaseModel):
    conversation_id: str
    response_type: str
    summary: str
    purchased_product_id: str | None = None
    trace: list[dict[str, Any]] = []
    products: list[dict[str, Any]] = []
    alternatives: list[dict[str, Any]] = []
    guidance: dict[str, Any] | None = None


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


# ── auth endpoints ─────────────────────────────────────────────────────

@app.post("/auth/register", response_model=TokenResponse)
async def register(body: RegisterRequest) -> TokenResponse:
    try:
        user = auth.create_user(body.email, body.password, body.name)
    except auth.AuthError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    token = auth.create_access_token(user["id"])
    return TokenResponse(access_token=token, user=UserResponse(**user))


@app.post("/auth/login", response_model=TokenResponse)
async def login(body: LoginRequest) -> TokenResponse:
    try:
        user = auth.authenticate_user(body.email, body.password)
    except auth.AuthError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from None
    token = auth.create_access_token(user["id"])
    return TokenResponse(access_token=token, user=UserResponse(**user))


@app.get("/auth/me", response_model=UserResponse)
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


@app.get("/cart")
async def get_cart(user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.get_cart(user.id))


@app.post("/cart/items")
async def add_cart_item(body: CartItemRequest, user: UserResponse = Depends(get_current_user)) -> dict:
    if body.product_id not in _agent.repository.by_id:
        raise HTTPException(status_code=404, detail="商品不存在")
    return _enrich_cart(store.add_cart_item(user.id, body.product_id, body.quantity))


@app.patch("/cart/items/{item_id}")
async def update_cart_item(item_id: str, body: CartItemUpdate, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_cart(store.update_cart_item(user.id, item_id, body.quantity))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


@app.delete("/cart/items/{item_id}")
async def remove_cart_item(item_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.remove_cart_item(user.id, item_id))


@app.delete("/cart")
async def clear_cart(user: UserResponse = Depends(get_current_user)) -> dict:
    return _enrich_cart(store.clear_cart(user.id))


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


@app.post("/orders")
async def create_order(body: OrderCreateRequest, user: UserResponse = Depends(get_current_user)) -> dict:
    cart = store.get_cart(user.id)
    lines = []
    for item in cart["items"]:
        product = _agent.repository.by_id.get(item["product_id"])
        if product is None:
            continue
        lines.append({"product_id": item["product_id"], "quantity": item["quantity"], "price": product.price})
    try:
        order = store.create_order(user.id, lines)
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None
    # 下单后清空购物车
    store.clear_cart(user.id)
    return _enrich_order(order)


@app.get("/orders")
async def list_orders(user: UserResponse = Depends(get_current_user)) -> list[dict]:
    return [_enrich_order(o) for o in store.list_orders(user.id)]


@app.get("/orders/{order_id}")
async def get_order(order_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_order(store.get_order(user.id, order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from None


@app.post("/orders/{order_id}/cancel")
async def cancel_order(order_id: str, user: UserResponse = Depends(get_current_user)) -> dict:
    try:
        return _enrich_order(store.cancel_order(user.id, order_id))
    except store.StoreError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from None


# ── health ─────────────────────────────────────────────────────────────

@app.get("/health")
async def health() -> dict[str, Any]:
    snapshot = _agent.observability_snapshot()
    return {
        "status": "ok",
        "model": snapshot,
        "catalog_products": len(_agent.repository.products),
    }


# ── conversations ──────────────────────────────────────────────────────

@app.post("/api/conversations")
async def create_conversation() -> dict[str, str]:
    state = ConversationState()
    _agent.restore_local_session(state)
    _conversations[state.conversation_id] = state
    return {"conversation_id": state.conversation_id}


@app.get("/api/conversations/{conversation_id}")
async def get_conversation(conversation_id: str) -> dict[str, Any]:
    state = _conversations.get(conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    return state.to_dict()


@app.post("/api/conversations/{conversation_id}/messages")
async def send_message(conversation_id: str, body: MessageRequest) -> TurnResponse:
    state = _conversations.get(conversation_id)
    if state is None:
        raise HTTPException(status_code=404, detail="Conversation not found")
    result = _agent.run_turn(body.message, state)
    catalog = result.get("catalog_data") or {}
    return TurnResponse(
        conversation_id=conversation_id,
        response_type=result.get("response_type", "unknown"),
        summary=result.get("summary", ""),
        purchased_product_id=result.get("purchased_product_id"),
        trace=result.get("trace", []),
        products=_extract_products(result),
        alternatives=_extract_alternatives(catalog),
        guidance=result.get("proactive_guidance"),
    )


# ── products ───────────────────────────────────────────────────────────

@app.get("/api/products")
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


@app.get("/api/products/{product_id}")
async def get_product(product_id: str) -> dict[str, Any]:
    product = _agent.repository.by_id.get(product_id.upper())
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product.to_dict()


# ── catalog ────────────────────────────────────────────────────────────

@app.get("/api/catalog/facets")
async def catalog_facets() -> dict[str, Any]:
    catalog = _agent.repository.catalog()
    return {
        "item_types": catalog["item_types"],
        "manufacturers": catalog["manufacturers"],
        "tags": catalog["tags"],
        "available_fields": _agent.repository.available_fields,
        "total_products": len(_agent.repository.products),
    }

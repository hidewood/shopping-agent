"""Cart and order persistence (SQLite).

MVP: one active cart per user; orders created from the cart with a simple
status state machine (pending → confirmed → shipped → delivered / cancelled).
Order creation is simulated — no real payment is performed.
"""

from __future__ import annotations

import sqlite3
import uuid
import hashlib
import hmac
import json
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / "local_state" / "store.db"

ORDER_STATUSES = {"pending", "confirmed", "shipped", "delivered", "cancelled"}


class StoreError(Exception):
    """Raised when a cart/order operation is invalid."""


def configure_db_path(path: str | Path) -> None:
    """Point the module at an isolated SQLite database (primarily for tests)."""
    global DB_PATH
    DB_PATH = Path(path)
    init_db()


def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 5000")
    conn.execute("PRAGMA journal_mode = WAL")
    return conn


def init_db() -> None:
    conn = _connection()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS carts (
            id         TEXT PRIMARY KEY,
            user_id    TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS cart_items (
            id         TEXT PRIMARY KEY,
            cart_id    TEXT NOT NULL REFERENCES carts(id),
            product_id TEXT NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            added_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS orders (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            status       TEXT NOT NULL DEFAULT 'confirmed',
            total_price  REAL NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS order_items (
            id         TEXT PRIMARY KEY,
            order_id   TEXT NOT NULL REFERENCES orders(id),
            product_id TEXT NOT NULL,
            quantity   INTEGER NOT NULL DEFAULT 1,
            unit_price REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS favorites (
            user_id    TEXT NOT NULL,
            product_id TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (user_id, product_id)
        );
        CREATE TABLE IF NOT EXISTS user_preferences (
            id           TEXT PRIMARY KEY,
            user_id      TEXT NOT NULL,
            preference   TEXT NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS conversations (
            conversation_id TEXT PRIMARY KEY,
            user_id         TEXT,
            guest_token_hash TEXT,
            title           TEXT,
            state_json      TEXT NOT NULL,
            revision        INTEGER NOT NULL DEFAULT 0,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS conversation_turns (
            conversation_id TEXT NOT NULL REFERENCES conversations(conversation_id) ON DELETE CASCADE,
            message_id      TEXT NOT NULL,
            response_json   TEXT NOT NULL,
            created_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (conversation_id, message_id)
        );
        """
    )
    # Lightweight optimistic concurrency for concurrent browser tabs/processes.
    conversation_columns = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "revision" not in conversation_columns:
        conn.execute("ALTER TABLE conversations ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
    cols = [r["name"] for r in conn.execute("PRAGMA table_info(conversations)").fetchall()]
    if "guest_token_hash" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN guest_token_hash TEXT")
    if "title" not in cols:
        conn.execute("ALTER TABLE conversations ADD COLUMN title TEXT")
    # Repair duplicate rows left by pre-V3 versions before enforcing the
    # one-cart/one-product invariants.
    duplicate_users = conn.execute(
        "SELECT user_id FROM carts GROUP BY user_id HAVING COUNT(*) > 1"
    ).fetchall()
    for duplicate in duplicate_users:
        carts = conn.execute(
            "SELECT id FROM carts WHERE user_id = ? ORDER BY created_at, rowid",
            (duplicate["user_id"],),
        ).fetchall()
        primary = carts[0]["id"]
        for cart in carts[1:]:
            for item in conn.execute(
                "SELECT product_id, quantity FROM cart_items WHERE cart_id = ?", (cart["id"],)
            ).fetchall():
                existing = conn.execute(
                    "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
                    (primary, item["product_id"]),
                ).fetchone()
                if existing:
                    conn.execute(
                        "UPDATE cart_items SET quantity = ? WHERE id = ?",
                        (min(99, int(existing["quantity"]) + int(item["quantity"])), existing["id"]),
                    )
                else:
                    conn.execute(
                        "UPDATE cart_items SET cart_id = ? WHERE cart_id = ? AND product_id = ?",
                        (primary, cart["id"], item["product_id"]),
                    )
            conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart["id"],))
            conn.execute("DELETE FROM carts WHERE id = ?", (cart["id"],))
    duplicate_items = conn.execute(
        "SELECT cart_id, product_id FROM cart_items GROUP BY cart_id, product_id HAVING COUNT(*) > 1"
    ).fetchall()
    for duplicate in duplicate_items:
        items = conn.execute(
            "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND product_id = ? ORDER BY added_at, rowid",
            (duplicate["cart_id"], duplicate["product_id"]),
        ).fetchall()
        conn.execute(
            "UPDATE cart_items SET quantity = ? WHERE id = ?",
            (min(99, sum(int(item["quantity"]) for item in items)), items[0]["id"]),
        )
        for item in items[1:]:
            conn.execute("DELETE FROM cart_items WHERE id = ?", (item["id"],))
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_carts_user ON carts(user_id)")
    conn.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_cart_product ON cart_items(cart_id, product_id)")
    conn.commit()
    conn.close()


# ── cart ───────────────────────────────────────────────────────────────

def _get_or_create_cart_id(conn: sqlite3.Connection, user_id: str) -> str:
    row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if row is not None:
        return row["id"]
    cart_id = uuid.uuid4().hex
    conn.execute("INSERT OR IGNORE INTO carts (id, user_id) VALUES (?, ?)", (cart_id, user_id))
    row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if row is None:
        raise StoreError("无法创建购物车")
    return row["id"]


def add_cart_item(user_id: str, product_id: str, quantity: int = 1) -> dict:
    if quantity < 1 or quantity > 99:
        raise StoreError("数量需在 1-99 之间")
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    conn.execute(
        """
        INSERT INTO cart_items (id, cart_id, product_id, quantity) VALUES (?, ?, ?, ?)
        ON CONFLICT(cart_id, product_id) DO UPDATE SET
            quantity = MIN(99, cart_items.quantity + excluded.quantity)
        """,
        (uuid.uuid4().hex, cart_id, product_id, quantity),
    )
    conn.commit()
    conn.close()
    return get_cart(user_id)


def update_cart_item(user_id: str, item_id: str, quantity: int) -> dict:
    if quantity < 1 or quantity > 99:
        raise StoreError("数量需在 1-99 之间")
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    row = conn.execute(
        "SELECT id FROM cart_items WHERE id = ? AND cart_id = ?", (item_id, cart_id)
    ).fetchone()
    if row is None:
        conn.close()
        raise StoreError("购物车中没有该商品")
    conn.execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (quantity, item_id))
    conn.commit()
    conn.close()
    return get_cart(user_id)


def remove_cart_item(user_id: str, item_id: str) -> dict:
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    conn.execute("DELETE FROM cart_items WHERE id = ? AND cart_id = ?", (item_id, cart_id))
    conn.commit()
    conn.close()
    return get_cart(user_id)


def clear_cart(user_id: str) -> dict:
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
    conn.commit()
    conn.close()
    return get_cart(user_id)


def get_cart(user_id: str) -> dict:
    """Return the cart with line items (product_id + quantity)."""
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    rows = conn.execute(
        "SELECT id, product_id, quantity FROM cart_items WHERE cart_id = ? ORDER BY added_at",
        (cart_id,),
    ).fetchall()
    # Persist a newly created empty cart as well.
    conn.commit()
    conn.close()
    items = [
        {"id": r["id"], "product_id": r["product_id"], "quantity": r["quantity"]}
        for r in rows
    ]
    return {"cart_id": cart_id, "items": items}


# ── favorites ──────────────────────────────────────────────────────────

def add_favorite(user_id: str, product_id: str) -> None:
    """收藏商品（重复收藏幂等，不报错）。"""
    conn = _connection()
    conn.execute(
        "INSERT OR IGNORE INTO favorites (user_id, product_id, created_at) VALUES (?, ?, ?)",
        (user_id, product_id, datetime.now(timezone.utc).isoformat()),
    )
    conn.commit()
    conn.close()


def remove_favorite(user_id: str, product_id: str) -> None:
    """取消收藏商品（不存在时静默成功）。"""
    conn = _connection()
    conn.execute(
        "DELETE FROM favorites WHERE user_id = ? AND product_id = ?",
        (user_id, product_id),
    )
    conn.commit()
    conn.close()


def list_favorites(user_id: str) -> list[dict]:
    """返回收藏列表，每项含 product_id 与 created_at。"""
    conn = _connection()
    rows = conn.execute(
        "SELECT product_id, created_at FROM favorites WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [{"product_id": r["product_id"], "created_at": r["created_at"]} for r in rows]


# ── user semantic preferences ─────────────────────────────────────────

def add_preference(user_id: str, preference: str) -> None:
    """记录用户对话中表达的语义偏好（如"清新风格"），幂等。"""
    preference = preference.strip()
    if not preference:
        return
    conn = _connection()
    conn.execute(
        "INSERT OR IGNORE INTO user_preferences (id, user_id, preference) VALUES (?, ?, ?)",
        (uuid.uuid4().hex, user_id, preference),
    )
    conn.commit()
    conn.close()


def list_preferences(user_id: str) -> list[str]:
    """返回用户的语义偏好列表（最近的在前）。"""
    conn = _connection()
    rows = conn.execute(
        "SELECT preference FROM user_preferences WHERE user_id = ? ORDER BY created_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [r["preference"] for r in rows]


# ── orders ─────────────────────────────────────────────────────────────

def create_order(user_id: str, products: list[dict]) -> dict:
    """Create an order from a list of {product_id, quantity, price} lines."""
    if not products:
        raise StoreError("订单不能为空")
    conn = _connection()
    order_id = f"ORD-{uuid.uuid4().hex[:8].upper()}"
    total = round(sum(p["price"] * p["quantity"] for p in products), 2)
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT INTO orders (id, user_id, status, total_price, created_at, updated_at) VALUES (?, ?, 'confirmed', ?, ?, ?)",
        (order_id, user_id, total, now, now),
    )
    for p in products:
        conn.execute(
            "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
            (uuid.uuid4().hex, order_id, p["product_id"], p["quantity"], p["price"]),
        )
    conn.commit()
    conn.close()
    return get_order(user_id, order_id)


def create_order_from_cart(
    user_id: str,
    prices: dict[str, float],
    *,
    idempotency_key: str | None = None,
) -> dict:
    """Create an order and clear its cart in one transaction.

    A caller-provided idempotency key deterministically identifies the order,
    so a browser retry cannot charge or create twice.
    """
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        order_id = (
            "ORD-" + hashlib.sha256(f"{user_id}:{idempotency_key}".encode("utf-8")).hexdigest()[:12].upper()
            if idempotency_key
            else f"ORD-{uuid.uuid4().hex[:12].upper()}"
        )
        existing = conn.execute(
            "SELECT id FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
        ).fetchone()
        if existing is not None:
            conn.rollback()
            return get_order(user_id, order_id)

        cart_id = _get_or_create_cart_id(conn, user_id)
        rows = conn.execute(
            "SELECT product_id, quantity FROM cart_items WHERE cart_id = ? ORDER BY added_at",
            (cart_id,),
        ).fetchall()
        products = []
        for row in rows:
            product_id = str(row["product_id"])
            if product_id not in prices:
                raise StoreError(f"商品 {product_id} 已不存在，请刷新购物车")
            products.append({
                "product_id": product_id,
                "quantity": int(row["quantity"]),
                "price": float(prices[product_id]),
            })
        if not products:
            raise StoreError("订单不能为空")

        total = round(sum(p["price"] * p["quantity"] for p in products), 2)
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO orders (id, user_id, status, total_price, created_at, updated_at) VALUES (?, ?, 'confirmed', ?, ?, ?)",
            (order_id, user_id, total, now, now),
        )
        for product in products:
            conn.execute(
                "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    order_id,
                    product["product_id"],
                    product["quantity"],
                    product["price"],
                ),
            )
        conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        raise
    conn.close()
    return get_order(user_id, order_id)


def list_orders(user_id: str) -> list[dict]:
    conn = _connection()
    rows = conn.execute(
        "SELECT * FROM orders WHERE user_id = ? ORDER BY created_at DESC", (user_id,)
    ).fetchall()
    orders = []
    for r in rows:
        items = conn.execute(
            "SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = ?",
            (r["id"],),
        ).fetchall()
        orders.append({
            "order_id": r["id"],
            "status": r["status"],
            "total_price": r["total_price"],
            "created_at": r["created_at"],
            "items": [{"product_id": i["product_id"], "quantity": i["quantity"], "unit_price": i["unit_price"]} for i in items],
        })
    conn.close()
    return orders


def get_order(user_id: str, order_id: str) -> dict:
    conn = _connection()
    row = conn.execute(
        "SELECT * FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
    ).fetchone()
    if row is None:
        conn.close()
        raise StoreError("订单不存在")
    items = conn.execute(
        "SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = ?",
        (order_id,),
    ).fetchall()
    conn.close()
    return {
        "order_id": row["id"],
        "status": row["status"],
        "total_price": row["total_price"],
        "created_at": row["created_at"],
        "items": [{"product_id": i["product_id"], "quantity": i["quantity"], "unit_price": i["unit_price"]} for i in items],
    }


def cancel_order(user_id: str, order_id: str) -> dict:
    conn = _connection()
    row = conn.execute(
        "SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
    ).fetchone()
    if row is None:
        conn.close()
        raise StoreError("订单不存在")
    if row["status"] not in {"pending", "confirmed"}:
        conn.close()
        raise StoreError("该订单状态无法取消")
    conn.execute(
        "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), order_id),
    )
    conn.commit()
    conn.close()
    return get_order(user_id, order_id)


def ship_order(user_id: str, order_id: str) -> dict:
    """发货：仅 confirmed 状态的订单可发货。"""
    conn = _connection()
    row = conn.execute(
        "SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
    ).fetchone()
    if row is None:
        conn.close()
        raise StoreError("订单不存在")
    if row["status"] != "confirmed":
        conn.close()
        raise StoreError("该订单状态无法发货")
    conn.execute(
        "UPDATE orders SET status = 'shipped', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), order_id),
    )
    conn.commit()
    conn.close()
    return get_order(user_id, order_id)


def deliver_order(user_id: str, order_id: str) -> dict:
    """送达：仅 shipped 状态的订单可送达。"""
    conn = _connection()
    row = conn.execute(
        "SELECT status FROM orders WHERE id = ? AND user_id = ?", (order_id, user_id)
    ).fetchone()
    if row is None:
        conn.close()
        raise StoreError("订单不存在")
    if row["status"] != "shipped":
        conn.close()
        raise StoreError("该订单状态无法送达")
    conn.execute(
        "UPDATE orders SET status = 'delivered', updated_at = ? WHERE id = ?",
        (datetime.now(timezone.utc).isoformat(), order_id),
    )
    conn.commit()
    conn.close()
    return get_order(user_id, order_id)


# ── admin (cross-user order management) ────────────────────────────────

def list_all_orders() -> list[dict]:
    """列出所有用户的订单（管理员用）。"""
    conn = _connection()
    rows = conn.execute(
        "SELECT * FROM orders ORDER BY created_at DESC"
    ).fetchall()
    orders = []
    for r in rows:
        items = conn.execute(
            "SELECT product_id, quantity, unit_price FROM order_items WHERE order_id = ?",
            (r["id"],),
        ).fetchall()
        orders.append({
            "order_id": r["id"],
            "user_id": r["user_id"],
            "status": r["status"],
            "total_price": r["total_price"],
            "created_at": r["created_at"],
            "items": [{"product_id": i["product_id"], "quantity": i["quantity"], "unit_price": i["unit_price"]} for i in items],
        })
    conn.close()
    return orders


def _admin_transition(order_id: str, from_status: str, to_status: str, err_msg: str) -> dict:
    conn = _connection()
    row = conn.execute("SELECT user_id, status FROM orders WHERE id = ?", (order_id,)).fetchone()
    if row is None:
        conn.close()
        raise StoreError("订单不存在")
    if row["status"] != from_status:
        conn.close()
        raise StoreError(err_msg)
    conn.execute(
        "UPDATE orders SET status = ?, updated_at = ? WHERE id = ?",
        (to_status, datetime.now(timezone.utc).isoformat(), order_id),
    )
    conn.commit()
    conn.close()
    # 复用 get_order（需要 user_id）
    return get_order(row["user_id"], order_id)


def admin_ship_order(order_id: str) -> dict:
    return _admin_transition(order_id, "confirmed", "shipped", "该订单状态无法发货")


def admin_deliver_order(order_id: str) -> dict:
    return _admin_transition(order_id, "shipped", "delivered", "该订单状态无法送达")


# ── conversations ──────────────────────────────────────────────────────

def save_conversation(
    conversation_id: str,
    user_id: str | None,
    state_json: str,
    guest_token: str | None = None,
    title: str | None = None,
) -> None:
    """Upsert a conversation's serialized state."""
    conn = _connection()
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """
        INSERT INTO conversations (conversation_id, user_id, guest_token_hash, title, state_json, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(conversation_id) DO UPDATE SET
            user_id = excluded.user_id,
            guest_token_hash = COALESCE(excluded.guest_token_hash, conversations.guest_token_hash),
            title = COALESCE(excluded.title, conversations.title),
            state_json = excluded.state_json,
            updated_at = excluded.updated_at
        """,
        (
            conversation_id,
            user_id,
            _token_hash(guest_token) if guest_token else None,
            title,
            state_json,
            now,
            now,
        ),
    )
    conn.commit()
    conn.close()


def save_conversation_if_current(
    conversation_id: str,
    state_json: str,
    expected_revision: int,
    *,
    title: str | None = None,
) -> bool:
    """Persist only when the conversation was not changed by another request.

    The API also serializes turns in-process; this check protects the state log
    when two application processes share the same SQLite database.
    """
    conn = _connection()
    cursor = conn.execute(
        """
        UPDATE conversations
        SET state_json = ?, title = COALESCE(?, title), updated_at = ?, revision = revision + 1
        WHERE conversation_id = ? AND revision = ?
        """,
        (state_json, title, datetime.now(timezone.utc).isoformat(), conversation_id, expected_revision),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount == 1


def load_processed_turn(conversation_id: str, message_id: str) -> dict | None:
    """Return the public response for an already committed client message."""
    conn = _connection()
    row = conn.execute(
        "SELECT response_json FROM conversation_turns WHERE conversation_id = ? AND message_id = ?",
        (conversation_id, message_id),
    ).fetchone()
    conn.close()
    return json.loads(row["response_json"]) if row is not None else None


def _apply_turn_effect(
    conn: sqlite3.Connection,
    user_id: str | None,
    effect: dict,
) -> None:
    """Apply one validated V3 effect inside the conversation transaction."""
    kind = str(effect.get("kind", ""))
    if not kind:
        return
    if user_id is None:
        raise StoreError("游客不能执行账户级收藏、购物车或订单操作")
    if kind == "favorite.add":
        for product_id in effect.get("product_ids", []):
            conn.execute(
                "INSERT OR IGNORE INTO favorites (user_id, product_id, created_at) VALUES (?, ?, ?)",
                (user_id, str(product_id), datetime.now(timezone.utc).isoformat()),
            )
        return
    if kind == "cart.add":
        cart_id = _get_or_create_cart_id(conn, user_id)
        for item in effect.get("items", []):
            product_id = str(item.get("product_id", ""))
            quantity = int(item.get("quantity", 1))
            if not product_id or quantity < 1 or quantity > 99:
                raise StoreError("购物车操作包含无效商品或数量")
            row = conn.execute(
                "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
                (cart_id, product_id),
            ).fetchone()
            if row is None:
                conn.execute(
                    "INSERT INTO cart_items (id, cart_id, product_id, quantity) VALUES (?, ?, ?, ?)",
                    (uuid.uuid4().hex, cart_id, product_id, quantity),
                )
            else:
                conn.execute(
                    "UPDATE cart_items SET quantity = ? WHERE id = ?",
                    (min(99, int(row["quantity"]) + quantity), row["id"]),
                )
        return
    if kind == "order.create":
        items = list(effect.get("items") or [])
        if not items:
            raise StoreError("模拟订单不能为空")
        order_id = str(effect.get("order_id") or f"ORD-{uuid.uuid4().hex[:8].upper()}")
        existing = conn.execute(
            "SELECT user_id FROM orders WHERE id = ?", (order_id,)
        ).fetchone()
        if existing is not None:
            if existing["user_id"] != user_id:
                raise StoreError("订单幂等键冲突")
            return
        total = round(
            sum(float(item["price"]) * int(item.get("quantity", 1)) for item in items),
            2,
        )
        now = datetime.now(timezone.utc).isoformat()
        conn.execute(
            "INSERT INTO orders (id, user_id, status, total_price, created_at, updated_at) VALUES (?, ?, 'confirmed', ?, ?, ?)",
            (order_id, user_id, total, now, now),
        )
        for item in items:
            conn.execute(
                "INSERT INTO order_items (id, order_id, product_id, quantity, unit_price) VALUES (?, ?, ?, ?, ?)",
                (
                    uuid.uuid4().hex,
                    order_id,
                    str(item["product_id"]),
                    int(item.get("quantity", 1)),
                    float(item["price"]),
                ),
            )
        return
    if kind == "order.cancel":
        order_id = str(effect.get("order_id", ""))
        row = conn.execute(
            "SELECT status FROM orders WHERE id = ? AND user_id = ?",
            (order_id, user_id),
        ).fetchone()
        if row is None:
            raise StoreError("模拟订单不存在")
        if row["status"] not in {"pending", "confirmed"}:
            raise StoreError("该模拟订单状态无法取消")
        conn.execute(
            "UPDATE orders SET status = 'cancelled', updated_at = ? WHERE id = ?",
            (datetime.now(timezone.utc).isoformat(), order_id),
        )
        return
    raise StoreError(f"不支持的会话副作用：{kind}")


def commit_conversation_turn(
    *,
    conversation_id: str,
    expected_revision: int,
    state_json: str,
    response_json: str,
    message_id: str,
    user_id: str | None,
    effects: list[dict] | None = None,
    title: str | None = None,
) -> str:
    """Atomically commit the conversation and all validated account effects.

    Returns ``committed``, ``replayed`` or ``conflict``. A repeated message ID
    never applies its effects twice.
    """
    conn = _connection()
    try:
        conn.execute("BEGIN IMMEDIATE")
        duplicate = conn.execute(
            "SELECT 1 FROM conversation_turns WHERE conversation_id = ? AND message_id = ?",
            (conversation_id, message_id),
        ).fetchone()
        if duplicate is not None:
            conn.rollback()
            return "replayed"
        row = conn.execute(
            "SELECT revision, user_id FROM conversations WHERE conversation_id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None or int(row["revision"]) != expected_revision:
            conn.rollback()
            return "conflict"
        if row["user_id"] != user_id:
            conn.rollback()
            raise StoreError("会话所有者在提交期间发生变化")
        for effect in effects or []:
            _apply_turn_effect(conn, user_id, effect)
        cursor = conn.execute(
            """
            UPDATE conversations
            SET state_json = ?, title = COALESCE(?, title), updated_at = ?, revision = revision + 1
            WHERE conversation_id = ? AND revision = ?
            """,
            (
                state_json,
                title,
                datetime.now(timezone.utc).isoformat(),
                conversation_id,
                expected_revision,
            ),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return "conflict"
        conn.execute(
            "INSERT INTO conversation_turns (conversation_id, message_id, response_json) VALUES (?, ?, ?)",
            (conversation_id, message_id, response_json),
        )
        conn.commit()
        return "committed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def load_conversation(conversation_id: str) -> dict | None:
    """Return a conversation's state dict, or None if absent."""
    conn = _connection()
    row = conn.execute("SELECT state_json FROM conversations WHERE conversation_id = ?", (conversation_id,)).fetchone()
    conn.close()
    if row is None:
        return None
    import json
    return json.loads(row["state_json"])


def load_conversation_record(conversation_id: str) -> dict | None:
    """Return persisted conversation state plus its ownership metadata."""
    conn = _connection()
    row = conn.execute(
        "SELECT user_id, guest_token_hash, title, state_json, revision FROM conversations WHERE conversation_id = ?",
        (conversation_id,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    import json
    return {
        "user_id": row["user_id"],
        "guest_token_hash": row["guest_token_hash"],
        "title": row["title"],
        "revision": int(row["revision"]),
        "state": json.loads(row["state_json"]),
    }


def verify_guest_conversation_token(record: dict, token: str | None) -> bool:
    """Constant-time verification for a guest conversation's bearer token."""
    expected = record.get("guest_token_hash")
    if not expected or not token:
        return False
    return hmac.compare_digest(expected, _token_hash(token))


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def delete_conversation(conversation_id: str) -> None:
    conn = _connection()
    conn.execute("DELETE FROM conversations WHERE conversation_id = ?", (conversation_id,))
    conn.commit()
    conn.close()


def list_user_conversations(user_id: str) -> list[dict]:
    """Return a user's named conversations, most recently updated first."""
    conn = _connection()
    rows = conn.execute(
        "SELECT conversation_id, title, created_at, updated_at FROM conversations WHERE user_id = ? ORDER BY updated_at DESC",
        (user_id,),
    ).fetchall()
    conn.close()
    return [
        {"conversation_id": r["conversation_id"], "title": r["title"], "created_at": r["created_at"], "updated_at": r["updated_at"]}
        for r in rows
    ]


def rename_conversation(conversation_id: str, user_id: str, title: str) -> bool:
    """Rename one owned conversation without exposing guest conversations."""
    conn = _connection()
    cursor = conn.execute(
        "UPDATE conversations SET title = ?, updated_at = ? WHERE conversation_id = ? AND user_id = ?",
        (title, datetime.now(timezone.utc).isoformat(), conversation_id, user_id),
    )
    conn.commit()
    conn.close()
    return cursor.rowcount == 1


init_db()

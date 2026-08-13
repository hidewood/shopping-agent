"""Cart and order persistence (SQLite).

MVP: one active cart per user; orders created from the cart with a simple
status state machine (pending → confirmed → shipped → delivered / cancelled).
Order creation is simulated — no real payment is performed.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_PATH = PROJECT_DIR / "local_state" / "store.db"

ORDER_STATUSES = {"pending", "confirmed", "shipped", "delivered", "cancelled"}


class StoreError(Exception):
    """Raised when a cart/order operation is invalid."""


def _connection() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
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
        """
    )
    conn.commit()
    conn.close()


# ── cart ───────────────────────────────────────────────────────────────

def _get_or_create_cart_id(conn: sqlite3.Connection, user_id: str) -> str:
    row = conn.execute("SELECT id FROM carts WHERE user_id = ?", (user_id,)).fetchone()
    if row is not None:
        return row["id"]
    cart_id = uuid.uuid4().hex
    conn.execute("INSERT INTO carts (id, user_id) VALUES (?, ?)", (cart_id, user_id))
    conn.commit()
    return cart_id


def add_cart_item(user_id: str, product_id: str, quantity: int = 1) -> dict:
    if quantity < 1 or quantity > 99:
        raise StoreError("数量需在 1-99 之间")
    conn = _connection()
    cart_id = _get_or_create_cart_id(conn, user_id)
    # 若商品已在购物车，累加数量
    row = conn.execute(
        "SELECT id, quantity FROM cart_items WHERE cart_id = ? AND product_id = ?",
        (cart_id, product_id),
    ).fetchone()
    if row is not None:
        new_qty = min(99, row["quantity"] + quantity)
        conn.execute("UPDATE cart_items SET quantity = ? WHERE id = ?", (new_qty, row["id"]))
    else:
        conn.execute(
            "INSERT INTO cart_items (id, cart_id, product_id, quantity) VALUES (?, ?, ?, ?)",
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


init_db()

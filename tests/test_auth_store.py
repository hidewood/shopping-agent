"""Unit tests for auth (JWT + bcrypt + role) and store (cart/order/favorite/conversation)."""

from __future__ import annotations

import unittest
import uuid
from pathlib import Path
import sys

PROJECT_DIR = Path(__file__).resolve().parents[1]
if str(PROJECT_DIR) not in sys.path:
    sys.path.insert(0, str(PROJECT_DIR))

from starter import auth, store


def _unique_email() -> str:
    return f"test-{uuid.uuid4().hex[:10]}@example.com"


class AuthTests(unittest.TestCase):
    def setUp(self) -> None:
        self.email = _unique_email()
        self.user = auth.create_user(self.email, "secret123", "测试用户")

    def tearDown(self) -> None:
        conn = auth._connection()
        conn.execute("DELETE FROM users WHERE email = ?", (self.email,))
        conn.commit()
        conn.close()

    def test_register_and_login(self) -> None:
        u = auth.authenticate_user(self.email, "secret123")
        self.assertEqual(u["email"], self.email)
        self.assertEqual(u["name"], "测试用户")
        self.assertEqual(u["role"], "user")

    def test_duplicate_email_rejected(self) -> None:
        with self.assertRaises(auth.AuthError):
            auth.create_user(self.email, "another123")

    def test_wrong_password_rejected(self) -> None:
        with self.assertRaises(auth.AuthError):
            auth.authenticate_user(self.email, "wrong")

    def test_password_is_hashed_not_plaintext(self) -> None:
        conn = auth._connection()
        row = conn.execute("SELECT password_hash FROM users WHERE email = ?", (self.email,)).fetchone()
        conn.close()
        self.assertNotEqual(row["password_hash"], "secret123")
        self.assertTrue(row["password_hash"].startswith("$2b$"))

    def test_jwt_roundtrip(self) -> None:
        token = auth.create_access_token(self.user["id"])
        uid = auth.decode_access_token(token)
        self.assertEqual(uid, self.user["id"])

    def test_invalid_token_rejected(self) -> None:
        with self.assertRaises(auth.AuthError):
            auth.decode_access_token("invalid-token")

    def test_set_admin_role(self) -> None:
        auth.set_admin_role(self.email)
        u = auth.get_user_by_id(self.user["id"])
        self.assertEqual(u["role"], "admin")

    def test_list_users(self) -> None:
        users = auth.list_users()
        self.assertTrue(any(u["email"] == self.email for u in users))


class StoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.user_id = f"test-{uuid.uuid4().hex[:12]}"

    def tearDown(self) -> None:
        conn = store._connection()
        for table, col in [("cart_items", "cart_id"), ("carts", "user_id"),
                            ("orders", "user_id"), ("order_items", "order_id"),
                            ("favorites", "user_id"), ("conversations", "conversation_id")]:
            # 按 user_id 清理（conversations 用 user_id，cart_items 需先清 cart）
            pass
        # 简单清理：删除该用户的购物车、订单、收藏、会话
        conn.execute("DELETE FROM favorites WHERE user_id = ?", (self.user_id,))
        conn.execute("DELETE FROM orders WHERE user_id = ?", (self.user_id,))
        conn.execute("DELETE FROM conversations WHERE user_id = ?", (self.user_id,))
        cart = conn.execute("SELECT id FROM carts WHERE user_id = ?", (self.user_id,)).fetchone()
        if cart:
            conn.execute("DELETE FROM cart_items WHERE cart_id = ?", (cart["id"],))
            conn.execute("DELETE FROM carts WHERE id = ?", (cart["id"],))
        conn.commit()
        conn.close()

    def test_cart_add_and_list(self) -> None:
        store.add_cart_item(self.user_id, "P0005", 2)
        store.add_cart_item(self.user_id, "P0011", 1)
        cart = store.get_cart(self.user_id)
        self.assertEqual(len(cart["items"]), 2)

    def test_cart_add_same_product_accumulates(self) -> None:
        store.add_cart_item(self.user_id, "P0005", 2)
        store.add_cart_item(self.user_id, "P0005", 3)
        cart = store.get_cart(self.user_id)
        self.assertEqual(cart["items"][0]["quantity"], 5)

    def test_cart_quantity_bounds(self) -> None:
        with self.assertRaises(store.StoreError):
            store.add_cart_item(self.user_id, "P0005", 0)
        with self.assertRaises(store.StoreError):
            store.add_cart_item(self.user_id, "P0005", 100)

    def test_order_create_and_cancel(self) -> None:
        lines = [{"product_id": "P0005", "quantity": 2, "price": 9.99}]
        order = store.create_order(self.user_id, lines)
        self.assertEqual(order["status"], "confirmed")
        self.assertEqual(order["total_price"], 19.98)
        cancelled = store.cancel_order(self.user_id, order["order_id"])
        self.assertEqual(cancelled["status"], "cancelled")

    def test_order_invalid_transition(self) -> None:
        order = store.create_order(self.user_id, [{"product_id": "P0005", "quantity": 1, "price": 9.99}])
        # 已 confirmed，不能直接 deliver
        with self.assertRaises(store.StoreError):
            store.deliver_order(self.user_id, order["order_id"])
        # ship 后再 ship 也拒绝
        store.ship_order(self.user_id, order["order_id"])
        with self.assertRaises(store.StoreError):
            store.ship_order(self.user_id, order["order_id"])

    def test_order_ship_and_deliver(self) -> None:
        order = store.create_order(self.user_id, [{"product_id": "P0005", "quantity": 1, "price": 9.99}])
        shipped = store.ship_order(self.user_id, order["order_id"])
        self.assertEqual(shipped["status"], "shipped")
        delivered = store.deliver_order(self.user_id, order["order_id"])
        self.assertEqual(delivered["status"], "delivered")

    def test_favorite_add_remove_list(self) -> None:
        store.add_favorite(self.user_id, "P0005")
        store.add_favorite(self.user_id, "P0011")
        favs = store.list_favorites(self.user_id)
        self.assertEqual(len(favs), 2)
        store.remove_favorite(self.user_id, "P0005")
        favs = store.list_favorites(self.user_id)
        self.assertEqual(len(favs), 1)
        self.assertEqual(favs[0]["product_id"], "P0011")

    def test_favorite_idempotent(self) -> None:
        store.add_favorite(self.user_id, "P0005")
        store.add_favorite(self.user_id, "P0005")  # 重复收藏不报错
        self.assertEqual(len(store.list_favorites(self.user_id)), 1)

    def test_conversation_save_load_delete(self) -> None:
        cid = f"conv-{uuid.uuid4().hex[:12]}"
        store.save_conversation(cid, self.user_id, '{"turn_count": 1}')
        self.assertEqual(store.load_conversation(cid), {"turn_count": 1})
        store.delete_conversation(cid)
        self.assertIsNone(store.load_conversation(cid))

    def test_list_user_conversations(self) -> None:
        cid1 = f"conv-{uuid.uuid4().hex[:12]}"
        cid2 = f"conv-{uuid.uuid4().hex[:12]}"
        store.save_conversation(cid1, self.user_id, "{}")
        store.save_conversation(cid2, self.user_id, "{}")
        convs = store.list_user_conversations(self.user_id)
        self.assertEqual(len(convs), 2)


if __name__ == "__main__":
    unittest.main()

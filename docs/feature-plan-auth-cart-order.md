# 个人登录 / 购物车 / 订单 — 架构规划

> 本文为架构规划文档，暂不实现代码。用于指导后续开发。

## 1. 背景与目标

当前系统是**本地会话原型**：收藏和模拟订单保存在 `local_state/<conversation_id>.json`，无账号体系、无跨设备同步、无真实交易。目标是在保持现有 Agent 架构不变的前提下，渐进引入**用户认证、购物车、订单**三块能力，形成可演示的完整购物闭环。

关键约束：
- 现有 `CAPABILITY_REGISTRY` 中 `order.create` / `payment.create` 标记为 `unsupported` —— 引入订单后需升级为 `supported`（或新增能力点）
- 现有 `LocalSessionStore`（收藏/模拟订单）是本地实现，需要平滑迁移到服务端数据库
- Agent 核心（`ShoppingAgent`）是纯函数式 + 事件溯源，不应被认证逻辑污染 —— 认证/购物车/订单属于**外层服务**，不是 Agent 内部状态

---

## 2. 用户认证

### 2.1 方案：JWT（access + refresh token）

| 项 | 选择 |
|---|---|
| 认证方式 | JWT Bearer token |
| 密码存储 | bcrypt（`passlib[bcrypt]`） |
| access token | 15 分钟，`Authorization: Bearer <token>` |
| refresh token | 7 天，`/auth/refresh` 换新 |
| 会话 | 无状态，前端存 refresh token（httpOnly cookie 或 localStorage） |

### 2.2 端点

```
POST /auth/register     { email, password, name } → { user_id, tokens }
POST /auth/login        { email, password } → { user_id, tokens }
POST /auth/refresh      { refresh_token } → { tokens }
POST /auth/logout       (revoke refresh token)
GET  /auth/me           → 当前用户信息
```

### 2.3 依赖注入

FastAPI `Depends(get_current_user)` 从 JWT 解析 user_id，注入到需要认证的端点。Agent 对话端点可选认证（匿名也能聊，登录后能持久化购物车/收藏）。

---

## 3. 数据模型

起步用 **SQLite**（`sqlite3` / SQLAlchemy），后续可迁移 PostgreSQL（改连接串即可）。

```sql
-- 用户
CREATE TABLE users (
  id            TEXT PRIMARY KEY,          -- uuid
  email         TEXT UNIQUE NOT NULL,
  password_hash TEXT NOT NULL,
  name          TEXT,
  created_at    TIMESTAMP DEFAULT now()
);

-- 购物车（一个用户一个活跃购物车）
CREATE TABLE carts (
  id         TEXT PRIMARY KEY,
  user_id    TEXT NOT NULL REFERENCES users(id),
  created_at TIMESTAMP DEFAULT now()
);

CREATE TABLE cart_items (
  id         TEXT PRIMARY KEY,
  cart_id    TEXT NOT NULL REFERENCES carts(id),
  product_id TEXT NOT NULL,               -- P0000 格式
  quantity   INTEGER NOT NULL DEFAULT 1,
  added_at   TIMESTAMP DEFAULT now()
);

-- 订单
CREATE TABLE orders (
  id           TEXT PRIMARY KEY,          -- SIM-0001 → ORD-0001
  user_id      TEXT NOT NULL REFERENCES users(id),
  status       TEXT NOT NULL,             -- pending/confirmed/shipped/delivered/cancelled
  total_price  REAL NOT NULL,
  created_at   TIMESTAMP DEFAULT now(),
  updated_at   TIMESTAMP DEFAULT now()
);

CREATE TABLE order_items (
  id         TEXT PRIMARY KEY,
  order_id   TEXT NOT NULL REFERENCES orders(id),
  product_id TEXT NOT NULL,
  quantity   INTEGER NOT NULL DEFAULT 1,
  unit_price REAL NOT NULL
);

-- 收藏（迁移现有 local_state 的 saved_product_ids）
CREATE TABLE favorites (
  user_id     TEXT NOT NULL REFERENCES users(id),
  product_id  TEXT NOT NULL,
  created_at  TIMESTAMP DEFAULT now(),
  PRIMARY KEY (user_id, product_id)
);
```

### 3.1 用户偏好：是否需要独立偏好表？

**结论：MVP 阶段不需要，进阶阶段可加。**

现有 `PreferenceProfile`（manufacturer/tag/item_type 的 affinity 计数）是**派生数据**，可通过 SQL 从 `favorites` 和 `orders`（join `order_items` + `products`）重建，无需单独存表。

**进阶场景**才需要独立的 `user_preferences` 表：

| 场景 | 是否需要偏好表 |
|---|---|
| 收藏/订单行为派生的排序信号 | ❌ 从 favorites+orders 重建即可 |
| 显式偏好（"我喜欢复古风"） | ✅ 需要（行为表无法覆盖） |
| 浏览/搜索历史 | ✅ 需要独立的 `search_history` / `view_history` 表 |
| 长期跨会话偏好累积 | ✅ 需要 |

若加，表结构：

```sql
CREATE TABLE user_preferences (
  user_id    TEXT NOT NULL REFERENCES users(id),
  pref_type  TEXT NOT NULL,   -- 'manufacturer' | 'tag' | 'item_type'
  pref_value TEXT NOT NULL,   -- 'Bayer-and-Sons' / 'Ocean' / 'mug'
  weight     INTEGER NOT NULL DEFAULT 1,
  updated_at TIMESTAMP DEFAULT now(),
  PRIMARY KEY (user_id, pref_type, pref_value)
);
```

---

## 4. 购物车

### 4.1 端点

```
GET    /cart              → 当前购物车 + 商品明细
POST   /cart/items        { product_id, quantity } → 加入购物车
PATCH  /cart/items/{id}   { quantity } → 改数量
DELETE /cart/items/{id}   → 移除
DELETE /cart              → 清空
```

### 4.2 与现有 LocalSessionStore 的关系

| 现有（本地） | 目标（服务端） |
|---|---|
| `state.saved_product_ids`（收藏） | `favorites` 表 |
| `state.simulated_orders`（模拟订单） | `orders` 表 |
| `PreferenceProfile`（排序信号） | 从 `favorites` + `orders` 重建 |

**迁移路径**：`LocalSessionStore` 保留为匿名用户 fallback，登录用户走数据库。`PreferenceProfile` 在每次请求时从用户的收藏/订单记录重建，保持排序信号不变。

---

## 5. 订单

### 5.1 状态机

```
pending → confirmed → shipped → delivered
   ↓                      ↓
cancelled              cancelled
```

| 状态 | 含义 |
|---|---|
| `pending` | 已创建，待确认（模拟：创建即 confirmed） |
| `confirmed` | 已确认 |
| `shipped` | 已发货 |
| `delivered` | 已送达 |
| `cancelled` | 已取消 |

### 5.2 端点

```
POST   /orders          { cart_id } → 从购物车创建订单
GET    /orders          → 订单列表
GET    /orders/{id}     → 订单详情
POST   /orders/{id}/cancel  → 取消（仅 pending/confirmed）
```

### 5.3 与 CAPABILITY_REGISTRY 衔接

现有 `CAPABILITY_REGISTRY`：
```python
CAPABILITY_REGISTRY = {
    "order.create": "unsupported",   # ← 升级为 "supported"
    "order.cancel": "unsupported",   # ← 升级为 "supported"
    "payment.create": "unsupported", # 保持 unsupported（不接真实支付）
}
```

Agent 的 `_handle_action_request` 目前对 `order.create` 返回 `capability_unavailable`。引入订单后，改为：真实订单动作由**外层 FastAPI 服务**处理，Agent 的 `action` 路由保持"能力检查"，把订单执行权交给服务层。

---

## 6. 架构分层

```
┌─────────────────────────────────────────┐
│  Vue3 前端                               │
│  (聊天 + 登录页 + 购物车页 + 订单页)      │
└───────────────┬─────────────────────────┘
                │ REST (JWT)
┌───────────────┴─────────────────────────┐
│  FastAPI 服务层                          │
│  ├── /auth  (注册/登录/刷新)             │
│  ├── /cart  (购物车 CRUD)               │
│  ├── /orders (订单状态机)               │
│  └── /api/conversations (Agent 对话)    │
│       └── 调用 ShoppingAgent.run_turn    │
└───────────────┬─────────────────────────┘
                │
┌───────────────┴─────────────────────────┐
│  ShoppingAgent (核心，保持纯函数式)      │
│  - 不感知认证/购物车/订单                │
│  - 只做：理解需求 → 检索 → 推荐          │
└─────────────────────────────────────────┘
                │
┌───────────────┴─────────────────────────┐
│  数据层                                  │
│  - SQLite/PostgreSQL (users/cart/orders) │
│  - products.jsonl (商品目录)             │
│  - data/images/ (商品图片)               │
└─────────────────────────────────────────┘
```

---

## 7. 实施顺序建议

1. **Phase A（认证）**：users 表 + `/auth/*` 端点 + JWT 依赖注入 —— 让登录用户可持久化
2. **Phase B（购物车）**：carts/cart_items 表 + `/cart/*` 端点 + 前端购物车页
3. **Phase C（订单）**：orders/order_items 表 + `/orders/*` + 状态机 + 从购物车下单
4. **Phase D（收藏迁移）**：favorites 表替代 `saved_product_ids`，`PreferenceProfile` 从 DB 重建
5. **Phase E（能力升级）**：`CAPABILITY_REGISTRY` 的 order 能力点升级，Agent action 路由对接服务层

---

## 8. 技术栈增量

| 库 | 用途 |
|---|---|
| `python-jose[cryptography]` 或 `pyjwt` | JWT 签发/校验 |
| `passlib[bcrypt]` | 密码哈希 |
| `sqlalchemy`（可选） | ORM，简化迁移 |
| `pydantic` | 已有，请求/响应模型 |

> 若保持轻量，可只用标准库 `sqlite3` + 手写 SQL，不引入 SQLAlchemy。

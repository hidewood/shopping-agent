# 智能购物 Agent

一个面向本地电商目录的自然语言购物 Agent。用户可以用中英文逐步描述要购买的商品、预算、主题和厂商；系统先让大模型把话语转换为受限计划（TurnPlan），再由本地代码检索商品库、校验约束、稳定排序并解释结果。

项目完整覆盖了「需求理解 → 商品搜索 → 候选比较 → 约束检查 → 购买决策 → 结果说明」的 Agent 工作流，并进一步实现了**用户认证、收藏、购物车、订单、管理员后台、游客链路和数据持久化**，形成一个完整的购物闭环。

| 文档 | 作用 |
| --- | --- |
| [实验报告](docs/experiment-report.md) | 大模型使用方式、Agent 工作流、提示词/工具设计与结果分析 |
| [测试用例](docs/test-report.md) | 覆盖对话、检索、状态、异常与界面的人工测试输入及期望结果 |
| [手动测试样例](docs/manual-test-cases.md) | 63 条自然语言测试样例（按场景分类） |
| [发展规划](docs/DEVELOPMENT_PLAN.md) | 项目演进记录与待办 |

## 1. 核心设计原则

1. **大模型负责理解，不负责编造事实。** DeepSeek 仅输出本轮意图与需求增量，不直接决定商品或编造目录字段。
2. **商品库事实由代码负责。** 商品检索、计数、详情、比较、硬约束过滤和排序均只读取 `data/products.jsonl`。
3. **多轮对话以事件溯源状态机维护。** 用户可先说类型，再补充预算、主题或厂商；目录查询不会覆盖正在进行的选购任务。
4. **结果可核验。** 每一步都有结构化 Trace，推荐结果展示筛选条件、商品卡片和排序依据。
5. **边界诚实。** 真实订单、支付未接入执行器；收藏/购物车/订单为本地模拟，绝不触发真实交易。
6. **鲁棒性优先。** 对模型输出做了多层容错（JSON Schema 校验、字段归一化、JSON 修复、一次协议修复）。
7. **会话偏好可解释。** 用户收藏的商品属性会形成排序偏好信号，作为同价候选的次级排序依据；当前轮的显式约束始终优先。

## 2. 功能特性

### 2.1 购物对话（核心 Agent）

| 能力 | 示例 |
| --- | --- |
| 商品探索 | `我想买一个马克杯` → 展示价格区间、常见标签与目录样例 |
| 多轮筛选 | `预算20以内` → `海洋主题的` → 逐步追加约束 |
| 直接推荐 | `推荐一件T恤，不限预算和风格` |
| 目录问答 | `衬衫有什么价位？`、`最便宜的马克杯多少钱？` |
| 商品详情/比较 | `介绍 P0005`、`比较 P0005 和 P0011` |
| 无结果处理 | 如实报告无匹配 + 给出放宽一个条件后的最近方案 |
| 组合方案 | `马克杯和T恤，总预算20以内，给我组合方案`（per-item / combined 两种预算模式） |
| 中英文混合 | `find me a shirt, budget 20` |

### 2.2 电商闭环（服务端）

- **用户认证**：注册 / 登录 / JWT（bcrypt 密码哈希）
- **游客链路**：打开进入登录页，游客可浏览商品库和购物对话，收藏/购物车/订单需登录
- **收藏**：收藏商品影响后续推荐排序（"越用越懂你"）
- **购物车**：增删改查 + 数量控制 + 结算
- **订单**：下单 → 发货 → 送达 → 取消（完整状态机，本地模拟）
- **对话历史**：登录用户的对话持久化，可回顾并继续历史会话
- **管理员后台**：订单管理（发货/送达）、用户管理、商品查看

### 2.3 数据持久化

所有数据统一存储在 SQLite 数据库（`local_state/` 下，gitignored）：

| 数据 | 存储 |
| --- | --- |
| 账号（含角色） | `users.db` |
| 购物车 / 订单 / 收藏 / 对话记录 | `store.db` |

## 3. 技术架构

```
┌─────────────────────────────────────────┐
│  Vue3 前端（Apple 风三栏布局 + 毛玻璃）   │
│  登录 / 对话 / 商品库 / 购物车 / 订单 /    │
│  收藏 / 历史会话 / 管理后台               │
└───────────────┬─────────────────────────┘
                │ REST (JWT)
┌───────────────┴─────────────────────────┐
│  FastAPI 服务层                          │
│  ├── /auth    认证                       │
│  ├── /cart    购物车                     │
│  ├── /orders  订单 + 状态机              │
│  ├── /favorites 收藏                    │
│  ├── /api/conversations 对话（持久化）   │
│  ├── /api/products 商品检索             │
│  └── /admin   管理员                    │
└───────────────┬─────────────────────────┘
                │
┌───────────────┴─────────────────────────┐
│  ShoppingAgent（核心，纯函数式）          │
│  理解需求 → 检索 → 硬过滤 → 确定性排序    │
└───────────────┬─────────────────────────┘
                │
┌───────────────┴─────────────────────────┐
│  数据层                                  │
│  SQLite（users/store）+ products.jsonl  │
└─────────────────────────────────────────┘
```

**核心工作流**（一次用户轮）：

```
用户消息
  → 输入完整性检查 + 强意图信号提取
  → 确定性分支（本地收藏/组合方案/开放式推荐等）
  → DeepSeek 生成 TurnPlan（goal + target + requirement + catalog_operations）
  → JSON Schema 校验 + 字段归一化容错 + 一次协议修复
  → 路由：聊天 | 目录查询 | 商品详情/比较 | 选购推荐 | 交易能力检查
  → 选购时：状态归约 → 目录接地 → 硬过滤 → 显式偏好/会话偏好/价格排序 → Trace
```

**模型只负责规划，代码负责事实**：候选排序完全由 Python 确定性完成（显式偏好 → 会话收藏偏好 → 价格 → 商品 ID），不调用第二次 LLM。

## 4. 快速开始

环境要求：Python 3.10+、Node.js 18+。

### 4.1 后端

```bash
pip install -r requirements.txt
# 配置 .env（见下方环境变量）
python -m uvicorn starter.api:app --host 127.0.0.1 --port 8000 --reload
```

### 4.2 前端

```bash
cd frontend
npm install
npm run dev
```

访问 `http://localhost:5173`（Vite dev server 会把 `/api`、`/images`、`/avatars` 代理到后端 8000）。

### 4.3 环境变量（`.env`）

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL=deepseek-v4-pro
AGENT_MAX_CANDIDATES=8
DEEPSEEK_TIMEOUT_SECONDS=45
DEEPSEEK_MAX_RETRIES=1
DEEPSEEK_CIRCUIT_BREAKER_SECONDS=60
# 可选
# JWT_SECRET_KEY=your-strong-random-secret
# ADMIN_EMAIL=admin@example.com   # 注册该邮箱自动成为管理员
```

## 5. 测试

```bash
# 完整离线测试（156 项）
python -m unittest discover -s tests -v

# 真实 API 冒烟测试
$env:RUN_LIVE_API_TESTS="1"; python -m unittest tests.test_live_api_smoke -v

# 手动测试样例（63 条，调用真实 DeepSeek）
python tests/run_manual_tests.py

# 50 条公开任务评测
python tests/task_evaluation.py --output outputs/task-evaluation.json --markdown-output docs/task-evaluation.md
```

## 6. 项目结构

```text
starter/
  agent_interface.py   # Agent 核心：提示词、状态机、目录检索、确定性排序
  config.py            # 配置（环境变量）
  llm_client.py        # LLM Provider 抽象 + DeepSeekClient + 熔断器
  common.py            # 共享工具函数
  api.py               # FastAPI 端点（认证/购物车/订单/收藏/对话/管理员）
  auth.py              # 用户认证（SQLite + JWT + bcrypt）
  store.py             # 购物车/订单/收藏/对话持久化（SQLite）
frontend/              # Vue3 + TypeScript + Tailwind 前端
data/
  products.jsonl       # 1,740 件商品目录
  catalog_language.json # 中英文别名 + 限额配置
  images/              # 商品图片（1,746 张）
  avatars/             # 头像
  tasks.jsonl          # 50 条公开任务
tests/                 # 156 项离线测试 + 手动测试脚本
docs/                  # 实验报告、测试文档、发展规划
```

## 7. 数据来源与限制

`data/products.jsonl` 来自 [stockholmux/ecommerce-sample-set](https://github.com/stockholmux/ecommerce-sample-set)，许可证为 Creative Commons Attribution-Share Alike 3.0 Unported。商品图片、头像等素材来自该仓库。

这是一个可运行原型而非完整电商系统：未接入真实支付、库存预占、物流、退换货。收藏、购物车、订单均为本地模拟（SQLite），不触发真实交易。JWT secret 默认值仅用于开发，生产环境需配置强随机密钥。

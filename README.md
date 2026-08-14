# 智能购物 Agent

一个面向本地电商目录的自然语言购物 Agent。用户可以用中英文逐步描述要购买的商品、预算、主题和厂商；系统先让大模型把模糊话语做**语义归一化**并转换为受限计划（TurnPlan），再由本地代码检索商品库、硬过滤、确定性排序并解释结果。

核心链路只有三件事：**LLM 语义归一化 → TurnPlan 单轮规划 → 确定性检索排序**。每一轮只调用一次 DeepSeek，候选商品的排序完全由 Python 代码完成，不依赖第二次模型调用，也不依赖向量检索。

在此基础上，项目进一步实现了**用户认证、收藏、购物车、订单、管理员后台、游客链路和对话持久化**，形成一个完整的购物闭环。

| 文档 | 作用 |
| --- | --- |
| [实验报告](docs/experiment-report.md) | 大模型使用方式、Agent 工作流、提示词设计与结果分析 |
| [测试用例](docs/test-report.md) | 覆盖对话、检索、状态、异常与界面的人工测试输入及期望结果 |
| [手动测试样例](docs/manual-test-cases.md) | 63 条自然语言测试样例（按场景分类） |
| [发展规划](docs/DEVELOPMENT_PLAN.md) | 项目演进记录与待办 |

## 1. 核心设计原则

1. **大模型负责理解与归一化，不负责编造事实。** DeepSeek 把模糊主题映射到目录真实标签（如「小清新」→ `Nature`），并输出本轮意图与需求增量；它不直接决定商品，也不编造目录字段。
2. **商品库事实由代码负责。** 商品检索、计数、详情、比较、硬约束过滤和排序均只读取 `data/products.jsonl`，全程确定性、可复现。
3. **检索优先于追问。** 只要商品类型已知（`mug` 与 `shirt` 互斥，类型是唯一真正阻塞的缺口），就去检索真实商品并报告如何进一步收窄，而不是先追问预算或主题。
4. **多轮对话以事件溯源状态机维护。** 用户可先说类型，再补充预算、主题或厂商；目录查询不会覆盖正在进行的选购任务。
5. **结果可核验。** 每一步都有结构化 Trace，推荐结果展示筛选条件、商品卡片和排序依据。
6. **边界诚实。** 真实订单、支付未接入执行器；收藏、购物车、订单均为本地模拟，绝不触发真实交易。
7. **鲁棒性优先。** 对模型输出做了多层容错（JSON Schema 校验、字段归一化、JSON 修复、一次协议修复）。
8. **会话偏好可解释。** 用户收藏的商品属性与对话中表达的语义偏好会形成排序偏好信号，作为同价候选的次级排序依据；当前轮的显式约束始终优先。

## 2. 功能特性

### 2.1 购物对话（核心 Agent）

| 能力 | 示例 |
| --- | --- |
| 语义归一化 | `小清新风格的马克杯` → 自动映射到目录标签 `Nature` |
| 商品探索 | `我想买一个马克杯` → 展示价格区间、常见标签与目录样例 |
| 多轮筛选 | `预算20以内` → `海洋主题的` → 逐步追加约束 |
| 直接推荐 | `推荐一件T恤，不限预算和风格` |
| 目录问答 | `衬衫有什么价位？`、`最便宜的马克杯多少钱？` |
| 商品详情/比较 | `介绍 P0005`、`比较 P0005 和 P0011` |
| 无结果处理 | 如实报告无匹配 + 给出放宽一个条件后的最近方案 |
| 组合方案 | `马克杯和T恤，总预算20以内，给我组合方案`（per-item / combined 两种预算模式） |
| 中英文混合 | `find me a shirt, budget 20` |

### 2.2 智能体验

- **个性化推荐理由**：推荐时解释「为什么推荐它」（如「它是 Nature 主题，符合你想要的清新风格」）
- **主动对话引导**：目录查询、澄清、无结果、推荐等场景下给出可继续追问的示例话术
- **多轮记忆**：保留最近 6 条上下文 + 记录用户表达的语义偏好与场景意图（送礼/自用），跨轮复用
- **无结果贴心处理**：报告「只差 $X，要不要看看」这类放宽一个约束后的最近方案
- **比较能力**：按商品 ID 对比多件商品的属性与价格
- **收藏同步排序**：收藏的商品属性自动转化为排序偏好信号（「越用越懂你」）
- **上下文摘要压缩**：对话过长时自动压缩历史，保留有效约束

### 2.3 电商闭环（服务端）

- **用户认证**：注册 / 登录 / JWT（bcrypt 密码哈希，角色区分普通用户与管理员）
- **游客链路**：游客可浏览商品库和购物对话，收藏 / 购物车 / 订单需登录
- **收藏**：增删查，影响后续推荐排序
- **购物车**：增删改查 + 数量控制 + 结算
- **订单**：下单 → 发货 → 送达 → 取消（完整状态机，本地模拟）
- **对话历史**：登录用户的对话持久化，可回顾并继续历史会话
- **管理员后台**：订单管理（发货/送达）、用户管理、商品增删改查

### 2.4 数据持久化

所有数据统一存储在 SQLite 数据库（`local_state/` 下，gitignored）：

| 数据 | 存储 |
| --- | --- |
| 账号（含角色） | `users.db` |
| 购物车 / 订单 / 收藏 / 语义偏好 / 对话记录 | `store.db` |

## 3. 技术架构

**技术栈**

- 后端：FastAPI + SQLite + DeepSeek（`deepseek-v4-pro`）+ JWT + bcrypt
- 前端：Vue3 + TypeScript + Tailwind（Apple 风三栏布局 + 毛玻璃）
- 依赖（`requirements.txt`）：`openai`、`python-dotenv`、`streamlit`、`jsonschema`、`fastapi`、`uvicorn`、`pydantic`、`passlib`、`bcrypt`、`PyJWT`

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
│  语义归一化 → 规划 → 检索 → 硬过滤 → 排序 │
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
  → 高置信度确定性分支（收藏、组合方案、开放式推荐等）
  → DeepSeek 语义归一化 + 生成 TurnPlan（goal + target + requirement + catalog_operations）
  → JSON Schema 校验 + 字段归一化容错 + 一次协议修复
  → 路由：聊天 | 目录查询 | 商品详情/比较 | 选购推荐 | 交易能力检查
  → 选购时：状态归约 → 目录接地 → 硬过滤 → 偏好/价格/ID 排序 → Trace
```

**语义归一化 + 确定性排序**：模糊主题（如「小清新」）由 DeepSeek 在规划阶段直接映射为目录真实标签（`Nature`），Python 只信任接地后的值进行检索；候选排序完全由代码完成（会话偏好 → 厂商匹配 → 标签命中 → 价格 → 商品 ID），**每轮仅一次模型调用，不调用第二次 LLM**。

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
JWT_SECRET_KEY=your-strong-random-secret
ADMIN_EMAIL=admin@example.com   # 注册该邮箱自动成为管理员
```

## 5. 测试

```bash
# 完整离线测试（180 项：145 核心 + 11 鲁棒性 + 24 认证/存储/对话状态）
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
starter/               # 后端核心（8 个文件）
  agent_interface.py   # Agent 核心：语义归一化、TurnPlan、状态机、检索、确定性排序
  api.py               # FastAPI 端点（认证/购物车/订单/收藏/对话/管理员）
  auth.py              # 用户认证（SQLite + JWT + bcrypt）
  store.py             # 购物车/订单/收藏/语义偏好/对话持久化（SQLite）
  catalog.py           # 商品目录管理（管理员增删改查）
  config.py            # 配置（环境变量）
  llm_client.py        # LLM 客户端抽象 + DeepSeekClient + 熔断器
  common.py            # 共享工具函数
frontend/              # Vue3 + TypeScript + Tailwind 前端（8 个页面）
data/
  products.jsonl       # 1,740 件商品目录（mug 870 / shirt 870）
  catalog_language.json # 中英文别名 + 限额配置
  images/              # 商品图片
  avatars/             # 头像
  tasks.jsonl          # 50 条公开评测任务
tests/                 # 180 项离线测试 + 手动测试脚本
docs/                  # 实验报告、测试文档、发展规划
```

## 7. 数据来源与限制

`data/products.jsonl` 来自 [stockholmux/ecommerce-sample-set](https://github.com/stockholmux/ecommerce-sample-set)，许可证为 Creative Commons Attribution-Share Alike 3.0 Unported。商品图片、头像等素材来自该仓库。

这是一个可运行原型而非完整电商系统：未接入真实支付、库存预占、物流、退换货。收藏、购物车、订单均为本地模拟（SQLite），不触发真实交易。JWT secret 默认值仅用于开发，生产环境需配置强随机密钥。

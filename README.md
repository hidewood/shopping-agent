# 智能购物 Agent

面向固定本地目录的可靠对话式购物系统。DeepSeek 只把自然语言编译为有序、受限的 `TurnProgram`（≤8 个语义子句）；Python 负责证据校验、`PurchasePlan` 归约、目录接地、硬过滤、组合求解和账户操作。单件、多人、多件和无偏好需求使用同一条执行链，不依赖 RAG、ReAct 或多 Agent 循环。

## 能力与边界

- 支持中英文目录问答、单/多人选购、局部增删改、单价或总预算、详情比较和无结果解释。
- **自然语言回复**：最终回复由模型根据「接地后的事实」生成（如「这款 Rustic Ocean Mug 带有海洋主题，价格 9.99，在您的预算内」），而非机械模板；事实仍由 Python 提供，模型不能编造。
- **模糊主题接地**：别名表 + 名称/描述确定性匹配，支持「小清新→Nature」「海边→Beach」「Strawberry→Strawberries」这类词，命中不了才澄清、绝不硬猜。
- **账户读写**：对话既能收藏 / 加购 / 下单 / 取消，也能回答「我有哪些订单」「我的收藏」「购物车有什么」。
- **换一批**：「再推荐点别的」会排除已展示商品、取下一批。
- 每轮只允许一个活动采购计划；只读问题不改计划，推荐结果以目录版本 + 计划版本快照保存。
- 登录用户的收藏、购物车、模拟订单和会话统一写入 SQLite；会话状态与账户副作用原子提交、按消息 ID 幂等（双击/重试不重复执行）。
- 目录共 **1,740** 件商品：`mug` 870、`shirt` 870；`products.jsonl` 是商品事实源。
- 不提供真实支付、库存、物流或退换货。订单及管理员发货状态仅用于本地流程演示。

## 本地启动

```powershell
pip install -r requirements.txt
Copy-Item .env.example .env
# 编辑 .env，至少设置 DEEPSEEK_API_KEY
python -m uvicorn starter.api:app --reload --port 8000

cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。默认关闭模型思考模式和 SDK 自动重试；语义编译与最多一次无副作用协议修复共享 30 秒预算。前端 3 秒后显示阶段提示，32 秒停止等待；重试复用消息 ID，不会重复收藏、加购或创建订单。

## 部署（Cloudflare Tunnel）

**方案 A：演示（dev server，一个 tunnel 指向前端）**

Vite dev server 已配好代理，会把 `/api`、`/auth`、`/cart`、`/orders`、`/favorites`、`/admin`、`/images`、`/avatars`、`/health` 全部转发到本地 8000。因此只需一个 tunnel 指向前端：

```bash
# 终端1：后端
python -m uvicorn starter.api:app --port 8000
# 终端2：前端
cd frontend && npm run dev
# 终端3：tunnel 指向前端（Vite 已设 allowedHosts=true，可通过域名访问）
cloudflared tunnel run <TUNNEL_ID>
```

`~/.cloudflared/config.yml` 的 ingress 把域名指向 `http://localhost:5173` 即可。

**方案 B：正式部署（构建前端 + 后端托管，一个 tunnel 指向 8000）**

```bash
cd frontend && npm run build    # 产出 frontend/dist/
# 让 FastAPI 托管 dist（api.py 挂载静态文件 + SPA fallback）
# tunnel 指向 8000
```

> 注意：方案 A 用的是 dev server，适合演示；长期公开请用方案 B，并考虑给 API 加注册门槛 + 限流，防止他人消耗 DeepSeek 配额。

## 管理员配置

首次启动前设置 `ADMIN_EMAIL` 和至少 12 位的 `ADMIN_INITIAL_PASSWORD`，系统在该邮箱尚未注册时创建管理员；公开注册永远只创建普通用户。也可用 `set_admin_role(email)` 将已有账号提升为管理员。管理员从普通登录页进入，管理商品、用户及模拟订单的发货/送达状态。凭据只放在本地 `.env`。

登录会话默认使用第一条消息作为标题，也可在「历史会话」中重命名。游客会话由 `X-Conversation-Token` 保护，不能执行账户操作。

## 项目结构

```text
starter/
  v3_engine.py           TurnProgram、PurchasePlan、确定性执行器 + 两个提示词
  agent_interface.py     对话状态、旧会话一次性迁移与模型适配
  api.py                 FastAPI、认证边界、原子会话提交、账户读写注入
  store.py               SQLite 收藏、购物车、订单、会话与幂等记录
  auth.py                用户认证（JWT + bcrypt + 角色）
  catalog.py             商品目录 CRUD（管理员）
  config.py              配置（环境变量）
  llm_client.py          DeepSeekClient + 熔断器 + 观测
  common.py              文本归一化工具
frontend/src/            Vue 3、TypeScript、Pinia、路由和管理页面
data/
  products.jsonl         mug/shirt 商品目录（1,740 件）
  catalog_language.json  中英文别名 + 限额配置
  images/                商品图片
docs/
  experiment-report.md   实验报告（工作流/状态机/提示词设计）
  test-report.md         测试验收手册（唯一测试文档）
```

## 测试与文档

自动化测试代码和运行产物按项目约定只保留在开发者本地，不提交 GitHub。手动验收步骤见 [测试验收手册](docs/test-report.md)，设计依据与可复现结论见 [实验报告](docs/experiment-report.md)。

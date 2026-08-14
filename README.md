# 智能购物 Agent

一个面向本地商品目录的可靠对话式购物系统。模型只负责把用户语言转换为受限的 `TurnPlan`；代码负责目录事实、状态归约、硬约束过滤与稳定排序。因此默认路径不是 RAG、Tool Calling、ReAct 或多 Agent 循环。

## 能力与边界

- 中英文多轮选购、预算/类型替换、目录问答、详情比较、无结果放宽与组合方案。
- 登录用户持久化收藏、购物车、模拟订单与会话；游客会话使用一次性访问令牌保护。
- 目录共有 **1,740** 件商品：`mug` 870、`shirt` 870。
- 商品、收藏、购物车和订单仅用于本地演示；不连接支付、库存或物流服务。

`architecture-ai.png` 仅展示核心推荐决策链；前端、API、Agent、模型、SQLite 与本地目录共同构成完整系统边界。

## 三分钟启动

```powershell
pip install -r requirements.txt
# 在 .env 中设置 DEEPSEEK_API_KEY；可选设置 DEEPSEEK_MODEL
python -m uvicorn starter.api:app --reload --port 8000

cd frontend
npm install
npm run dev
```

打开 `http://localhost:5173`。开发服务会把 API 和静态目录请求代理到端口 8000。

### 管理员首次配置

在首次启动前，于 `.env` 同时设置 `ADMIN_EMAIL` 和 `ADMIN_INITIAL_PASSWORD`。服务会只创建一次该管理员账户并保存密码哈希；若该邮箱已注册，则只提升其角色，不改写已有密码。之后以该邮箱登录即可进入“管理中心”，管理商品、用户和模拟订单。不要把真实管理员密码写进仓库。

登录用户的会话会在第一条有效消息后自动以该消息命名，也可以在“历史会话”中重命名。

## 测试说明

测试脚本、测试配置和运行产物仅保留在本地，不纳入仓库。测试策略、人工验收项目和真实模型验收记录见 [测试验收手册](docs/test-report.md)。GitHub 不运行或保存测试自动化。

## 项目结构

```text
starter/     FastAPI、ShoppingAgent、认证与 SQLite 存储
frontend/    Vue 3 + TypeScript 界面
data/        products.jsonl、语言配置、商品图片与 50 条公开任务
docs/        报告、图源与渲染后的图像
```

## 文档导航

- [测试验收手册](docs/test-report.md)：分层策略、门禁、人工验收和失败分诊。
- [实验报告](docs/experiment-report.md)：方法、架构、状态机、实验与局限。
- [开发计划](docs/DEVELOPMENT_PLAN.md)：历史计划与演进记录。

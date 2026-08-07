# Shopping Agent Workflow

一个基于 DeepSeek API 的本地商品库购物 Agent 原型。每条消息先由模型生成受限的 `TurnPlan`，再由代码执行真实目录查询、状态迁移、硬约束过滤与结果校验；仅在推荐时额外让模型比较真实候选。网页支持有状态多轮对话、主动追问、目录聚合查询和可核验解释。

## 工作流

```text
单轮需求 / 对话新消息
  -> DeepSeek：统一 TurnPlan（一次）
     -> chat：自然客服回复（不访问商品库、不改变购物条件）
     -> catalog：代码执行 count / group_by / list / price 等目录操作
     -> recommendation：状态归约、筛选；DeepSeek 仅比较真实候选（第二次调用）
     -> product_detail / product_comparison：代码核验指定商品 ID 的真实字段
```

详细设计见 [docs/workflow.md](docs/workflow.md)。

## 项目结构

```text
data/                       # 完整商品库、公开任务与数据说明
starter/
  agent_interface.py        # 单文件 Agent：配置、提示词、对话状态、检索、校验和接口
docs/                       # 工作流设计与图形化测试场景
tests/                      # 不调用 API 的回归测试
app.py                      # Streamlit 图形化页面
```

## 安装

需要 Python 3.10 或更高版本。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

编辑 `.env`，填写自己的 DeepSeek API Key：

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL=deepseek-v4-pro
DEEPSEEK_TIMEOUT_SECONDS=20
DEEPSEEK_MAX_RETRIES=1
```

`.env` 已被 Git 忽略，禁止提交真实密钥。

## 运行

图形化页面：

```bash
streamlit run app.py
```

浏览器打开命令行显示的本地地址（默认 `http://localhost:8501`）。在“智能推荐”中可直接输入需求；如果信息不足，继续在底部输入框回答追问即可。点击“新建对话”会开启独立会话，不会继承旧条件。页面还提供商品库浏览、推荐结果和按需展开的核验依据。

测试任务与人工核验要点见 [docs/test-cases.md](docs/test-cases.md)。

## 验证

商品检索与硬约束逻辑无需 API 即可测试：

```bash
python -m unittest discover -s tests -v
```

图形化页面是项目唯一的用户交互入口；单元测试仅用于开发验证。

如需验证真实网络、代理、模型名和 JSON 计划遵从性，可在已配置 Key 的环境显式执行（会产生 API 调用）：

```powershell
$env:RUN_LIVE_API_TESTS=1
python -m unittest tests.test_live_api_smoke -v
```

## 设计边界与失败处理

- 模型解析的类别、厂商和标签提示必须先在商品目录中验证，目录外值不会直接参与过滤；
- 内部目录值保持英文；代码和提示词只使用一组可审查的中文常用别名（如 `衬衫 → shirt`、`海洋主题 → Ocean`）辅助对齐，未收录的品牌、风格或中文译名不会被擅自替换；
- 模型只能从代码提供的候选商品中选，最终 ID 由代码复核；
- 价格、已对齐的类别、硬性厂商和硬性标签由代码严格过滤；
- 风格、用途和“优先某厂商”等软偏好仅用于排序；软偏好得分相同时，代码强制低价优先；相近替代方案会在结果中说明；
- 无匹配时不编造商品，明确返回无结果；
- 模型/API/JSON 发生异常时，记录 `trace` 并明确返回模型服务错误；系统不会以本地规则替代模型决策；
- API 客户端默认 20 秒超时、最多重试一次；错误会按连接、超时、限流、认证、服务端状态或模型输出不合规分类，且不会显示敏感信息；
- 正常聊天、目录查询、商品详情与比较调用一次模型；正常购物推荐调用两次（统一计划、候选决策）。每个步骤均有固定上限，不存在无限循环。
- 对话状态以事件日志保存，并在每轮归约为当前需求；新预算会替换旧预算。类型冲突先比较经受控中英文对照和目录校验后的规范值，因此 `T恤`、`衬衫` 与 `shirt` 不会被误判为换类。
- 每条消息由 DeepSeek 输出一个经过严格校验的 `TurnPlan`；其 intent 为 `chat`、`catalog`、`recommendation`、`product_detail` 或 `product_comparison`。目录查询可组合多个操作，例如按标签/风格聚合；聊天、目录查询和服务错误均不会改写正在进行的购物追问。客服不得编造订单、配送、售后、库存或隐私政策事实。

## 数据来源

`data/products.jsonl` 已导入上游完整商品库，共 1,740 件商品。`metadata.json` 记录了数据源为 [stockholmux/ecommerce-sample-set](https://github.com/stockholmux/ecommerce-sample-set)，上游许可证为 Creative Commons Attribution-Share Alike 3.0 Unported。

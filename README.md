# 智能购物 Agent

一个面向本地电商目录的自然语言购物 Agent 原型。用户可以用中英文逐步描述要购买的商品、预算、主题和厂商；系统先让大模型把话语转换为受限计划，再由本地代码检索商品库、校验约束、稳定排序并解释结果。

本项目对应“Agent 工作流构建”任务：覆盖需求理解、商品搜索、候选比较、约束检查、购买决策与结果说明，并额外实现多轮填槽、主动引导、状态隔离和失败边界。

| 文档 | 作用 |
| --- | --- |
| [实验报告](docs/experiment-report.md) | 大模型使用方式、Agent 工作流、提示词/工具设计与结果分析。 |
| [测试报告](docs/test-report.md) | 自动化结果、A1–A18 人工场景与复测方法。 |

## 1. 设计目标

购物对话的难点不只是“找商品”，还包括把模糊表达、安全边界和可验证事实区分开。项目采用以下原则：

1. **大模型负责理解，不负责编造事实。** DeepSeek 仅输出本轮意图与需求增量，不直接决定商品或编造目录字段。
2. **商品库事实由代码负责。** 商品检索、计数、详情、比较、硬约束过滤和排序均只读取 `data/products.jsonl`。
3. **多轮对话以状态机维护。** 用户可先说商品类型，再补充预算、主题或厂商；目录查询和商品详情不会覆盖正在进行的选购任务。
4. **结果可核验。** 推荐结果展示筛选条件、商品卡片和可折叠的候选/Trace 依据。
5. **边界诚实。** 订单、支付和取消订单当前未接入执行器，系统会明确拒绝，不会伪称交易成功。

![系统概览：模型只负责 TurnPlan 规划，本地代码验证商品事实与排序](docs/screenshots/architecture-ai.png)

## 2. 能力与边界

| 能力 | 示例 | 执行方式 |
| --- | --- | --- |
| 商品探索 | `我想买一个马克杯` | 展示该类型的真实价格区间、常见标签与目录样例。 |
| 多轮筛选 | `Ocean主题，预算20元以内` | 合并已知商品类型，按主题与预算进行硬过滤。 |
| 直接推荐 | `我想买一件T恤，不限预算和风格，直接推荐一个` | 对明确开放式需求直接推荐，不强制用户填写全部槽位。 |
| 目录问答 | `衬衫有什么价位？` | 返回真实目录统计，不改变选购状态。 |
| 商品详情/比较 | `介绍 P0005`、`比较 P0005 和 P0011` | 仅根据真实商品 ID 返回字段。 |
| 无结果处理 | `Ocean主题马克杯，预算8元以内` | 如实报告无匹配，并给出只放宽一个条件后的最近方案。 |
| 交易请求 | `下单 P0005` | 明确提示当前不支持创建订单、支付或取消订单。 |

当前商品库共有 1,740 件商品，其中 `mug` 与 `shirt` 各 870 件。商品名称、厂商、标签与描述保留原始英文目录值；界面与对话支持中英文输入。

## 3. 工作流一览

```text
用户消息
  → 输入完整性检查与强意图信号提取
  → DeepSeek 生成受限 TurnPlan（或命中高置信状态规则）
  → JSON/字段组合/授权证据校验，必要时仅修复一次
  → 路由：聊天 | 目录查询 | 商品详情/比较 | 选购推荐 | 交易能力检查
  → 选购时：状态归约 → 目录接地 → 硬过滤 → 确定性排序 → Trace 展示
```

推荐按“已验证偏好命中数 → 价格从低到高 → 商品 ID”的稳定规则排序。商品类型、预算、明确主题与明确厂商属于硬约束；“喜欢”“优先”等表达仅影响排序，不会错误造成零结果。

完整状态机、异常分支和设计动机见[实验报告](docs/experiment-report.md)。

## 4. 快速开始

环境要求：Python 3.10+。

```bash
python -m venv .venv
```

Windows PowerShell：

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env
```

在 `.env` 中填写自己的 DeepSeek API Key：

```env
DEEPSEEK_API_KEY=your_api_key_here
DEEPSEEK_MODEL=deepseek-v4-flash
AGENT_MAX_CANDIDATES=8
DEEPSEEK_TIMEOUT_SECONDS=45
DEEPSEEK_MAX_RETRIES=1
```

`DEEPSEEK_MODEL` 可按账户实际可用模型调整；未设置时，代码默认使用 `deepseek-v4-pro`。`.env` 已被 Git 忽略，不能提交真实密钥。

启动界面：

```bash
streamlit run app.py
```

打开命令行显示的本地地址（通常为 `http://localhost:8501`）。点击“新建对话”可开启独立会话；推荐结果可展开查看备选商品、过滤数量和结构化核验记录。

## 5. 测试与复现

运行不依赖 API Key 的完整离线测试：

```bash
python -m unittest discover -s tests -v
```

截至 2026-08-10，该命令共运行 **112 项**：**111 项通过，1 项真实 API 冒烟测试因未配置 `RUN_LIVE_API_TESTS=1` 而跳过**。测试覆盖目录接地、状态归约、路由、价格区间、主动引导、无结果和近期修复的回归场景。

若已配置 API Key，可执行 50 条公开任务的真实 API 评测：

```bash
python tests/task_evaluation.py --output outputs/task-evaluation.json --markdown-output outputs/task-evaluation.md
```

该命令会在本地 `outputs/` 中生成可审阅记录，并核验返回商品的类型、主题、预算、严格厂商条件、可用偏好厂商与确定性排序 Trace。历史真实 API 记录为 50/50 通过；该结果的产生时间、核验范围和可复现方法见[测试报告](docs/test-report.md)。

## 6. 项目结构

```text
app.py                         # Streamlit 交互与结果展示
starter/agent_interface.py     # 模型调用、提示词、状态机、目录工具与决策
data/products.jsonl            # 1,740 件商品目录
data/tasks.jsonl               # 50 条公开模拟购物任务
tests/                         # 离线回归、真实 API 冒烟与任务评测脚本
docs/experiment-report.md      # 实验报告
docs/test-report.md            # 测试报告
docs/screenshots/              # 报告所用流程图与真实界面证据
```

## 7. 数据来源与当前限制

`data/products.jsonl` 来自 [stockholmux/ecommerce-sample-set](https://github.com/stockholmux/ecommerce-sample-set)，许可证为 Creative Commons Attribution-Share Alike 3.0 Unported。

这是一个可运行原型而非完整电商系统：未接入库存、订单、支付、物流、用户偏好持久化或真实商品图片；中文别名和模糊主题的覆盖范围也受目录标签限制。系统会把无法接地的硬条件明确标为无法满足，而不会虚构匹配结果。

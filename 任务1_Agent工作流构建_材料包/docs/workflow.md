# Shopping Agent 工作流设计

## 目标与边界

系统面向本地商品目录。DeepSeek API 只负责把用户自然语言翻译为受限的单轮计划（`TurnPlan`），以及在真实候选商品中撰写/选择推荐理由；Python 负责目录事实、状态迁移、硬约束过滤、操作执行和结果校验。系统没有离线模型或规则推荐兜底。

关键边界是：模型不能直接执行任意查询、改写会话状态或编造商品事实；程序也不替代模型理解用户语义。

## 1. 唯一执行路径

网页与 `Agent.run(instruction)` 都通过 `Agent.run_turn(message, state)` 执行。`run` 只是在一个新建会话上的兼容视图，不再维护独立单轮逻辑。

```text
用户消息
  → DeepSeek 单次 TurnPlan 规划
  → JSON Schema / 计划契约校验
  ├─ chat              → 显示自然客服回复
  ├─ catalog           → Python 执行受限目录操作
  ├─ recommendation    → 状态归约 → 检索/硬过滤 → DeepSeek 候选决策 → 校验
  ├─ product_detail    → Python 校验 ID 并返回真实字段
  └─ product_comparison→ Python 校验多个 ID 并并列返回真实字段
```

普通聊天和目录只读查询不会更新购物状态。推荐计划才允许产生购物条件更新。商品详情与比较的产品 ID 始终由 Python 从当前消息中提取和核验。

## 2. TurnPlan 契约

每轮第一个模型调用只能返回下列结构：

```json
{
  "intent": "catalog",
  "customer_reply": null,
  "requirement": {
    "item_type": {"raw_value": "衬衫", "constraint_strength": "hard", "catalog_hint": "shirt"},
    "manufacturer": null,
    "price_constraint": null,
    "concepts": [],
    "needs_clarification": false,
    "clarification_question": null
  },
  "catalog_operations": ["count", "group_by_tag"],
  "state_action": "none"
}
```

| intent | 允许的状态动作 | 允许的额外字段 | 程序行为 |
| --- | --- | --- | --- |
| `chat` | `none` | 仅 `customer_reply` | 显示客服回复，不读取目录。 |
| `catalog` | `none` | `requirement`、一个或多个目录操作 | 仅查询真实目录，不改写待推荐条件。 |
| `recommendation` | `merge` | `requirement` | 将本轮条件以事件形式合并/替换后检索。 |
| `product_detail` | `none` | 无 | 校验一个显式 `Pxxxx`。 |
| `product_comparison` | `none` | 无 | 校验两个或以上显式 `Pxxxx`。 |

任何不满足组合约束、枚举值或字段类型的计划都是 `invalid_model_output`，整轮失败而不执行目录操作或写入购物状态。

## 3. 受限目录操作

`catalog_operations` 只允许以下固定词汇：

| 操作 | 含义 |
| --- | --- |
| `count` | 返回符合过滤条件的真实商品数量。 |
| `group_by_item_type` | 按商品类型聚合。 |
| `group_by_manufacturer` | 按厂商聚合。 |
| `group_by_tag` | 按标签/风格聚合。 |
| `list` | 返回价格较低的前五件真实商品。 |
| `price_range` | 返回最低和最高价格及对应商品。 |
| `price_extreme` | 返回最低或最高价商品。 |

复合问题用多个操作表达，而不是靠特定问句分支。例如“有衬衫吗？都有哪些风格？”是 `count + group_by_tag`，筛选条件是 `item_type=shirt`。聚合结果均由商品库计算，标签、厂商和数量不会由模型生成。

## 4. 多轮状态

`ConversationState` 是可序列化事件日志。推荐轮次产生 `constraint_update`，reducer 在每轮开始时回放它们得到当前 `ShoppingRequirement`。预算可替换；规范类型不同才需要用户明确确认；受控中英文别名确保 `T恤`、`衬衫` 与 `shirt` 对齐为同一规范类型。

状态规则：

- `chat`、`catalog`、商品详情、商品比较和 `service_error` 不写购物约束；
- 失败轮次会回滚该轮已写入的 `constraint_update`；
- 模型规划上下文会排除历史 `service_error` 文案，避免一次网络故障影响后续问候或闲聊；
- 目录查询可以读取当前消息中的过滤条件，但不会覆盖未完成推荐的条件。

## 5. 目录对齐与推荐

目录规范值保持英文。模型可使用受控别名，例如 `马克杯 → mug`、`衬衫/T恤 → shirt`、`海洋主题 → Ocean`；别名仅在对应英文值存在于当前目录时生效，用户原话始终保留在 `raw_value`。不能把未知品牌或风格擅自映射到目录标签。

Python 先执行类别、预算、明确主题、硬厂商等硬约束过滤；风格、用途、优先厂商等软偏好仅参与排序。代码仅把前 `AGENT_MAX_CANDIDATES` 个真实候选交给第二次模型调用。模型必须选择候选 ID；非法、为空或违反低价优先规则的选择会被视为决策失败或被规则纠正。

因此正常聊天和目录查询只需一次模型调用；正常推荐最多两次（计划、候选决策），而不是旧架构的四次串行调用。

## 6. 故障与可观测性

模型异常被归类为 `configuration`、`connection`、`timeout`、`authentication`、`rate_limit`、`provider_status`、`model_request_error` 或 `invalid_model_output`。结果 trace 记录失败阶段和分类；页面显示脱敏的阶段/类别，不显示 API Key 或完整供应商响应。

API 客户端默认超时 20 秒，并允许至多一次可配置重试（`DEEPSEEK_MAX_RETRIES=1`，范围 0–2）。重试只针对客户端/服务端传输层；不会以本地推荐替代失败的模型语义判断。

## 7. 测试原则

测试案例是回归样本，不是生产规则。除具体中英文样例外，测试还验证以下性质：目录只读查询不改变购物状态；失败轮次不遗留约束；聚合字段必须来自真实目录；服务错误不进入后续模型上下文；计划必须通过 schema 组合校验。真实 API 冒烟测试用于验证网络、代理、模型名和实际 JSON 遵从性，不能由 Stub 测试替代。

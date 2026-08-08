# Shopping Agent 工作流设计

## 目标与边界

系统面向本地商品目录。DeepSeek API 负责把用户自然语言翻译为受限的单轮计划（`TurnPlan`）；Python 负责目录事实、状态迁移、硬约束过滤、候选排序、操作执行和结果校验。系统没有离线模型或规则推荐兜底：模型规划失败时，本轮不会进入目录检索或推荐。

关键边界是：模型不能直接执行任意查询、改写会话状态或编造商品事实；程序也不替代模型理解用户语义。

## 1. 唯一执行路径

网页与 `Agent.run(instruction)` 都通过 `Agent.run_turn(message, state)` 执行。`run` 只是在一个新建会话上的兼容视图，不再维护独立单轮逻辑。

```text
用户消息
  → DeepSeek 单次 TurnPlan 规划
  → JSON Schema / 计划契约校验
  ├─ goal=chat, target=none          → 显示自然客服回复
  ├─ goal=information, target=catalog→ Python 执行受限目录操作
  ├─ goal=information, target=product→ Python 校验 ID 后返回详情/比较
  ├─ goal=selection, target=catalog  → 策略校验 → 状态归约 → 检索/硬过滤 → 确定性候选排序 → 校验
  └─ goal=action, target=transaction → 能力注册表检查；未实现则明确拒绝，不伪装为查询
```

普通聊天和目录只读查询不会更新购物状态。只有 `goal=selection` 的计划才允许产生购物条件更新。商品详情、比较和交易请求的产品 ID 始终由 Python 从当前消息中提取和核验。

## 2. TurnPlan 契约

每轮第一个模型调用只能返回下列结构：

```json
{
  "goal": "information",
  "target": "catalog",
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
  "state_action": "none",
  "selection_mode": null,
  "action": null,
  "goal_evidence": ["有哪些风格的衬衫"]
}
```

| goal / target | 允许的状态动作 | 必填或允许的额外字段 | 程序行为 |
| --- | --- | --- | --- |
| `chat / none` | `none` | 仅 `customer_reply` | 显示客服回复，不读取目录。 |
| `information / catalog` | `none` | `requirement`、一个或多个目录操作 | 仅查询真实目录，不改写待推荐条件。 |
| `information / product` | `none` | 无 | 根据显式 `Pxxxx` 返回详情；两个或以上 ID 返回比较。 |
| `selection / catalog` | `merge` 或 `replace` | `requirement`、`selection_mode` | 先由推荐资格策略检查；`merge` 延续当前任务，`replace` 开启新任务后检索。 |
| `action / transaction` | `none` | `action` 为受控交易动作 | 查询能力注册表；当前订单、支付和取消订单明确为未实现。 |

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

它还保存独立的 `TaskContext`，记录当前工作流任务（`selection`、`information` 或 `action`）、推荐阶段（收集条件、已推荐、无匹配）、最近查看的目录/商品范围及当前焦点商品。需求状态回答“买什么”，任务状态回答“正在做什么”；例如插入一次“衬衫有哪些价位”的目录浏览时，待补充的推荐任务仍保持在 `selection / collecting`，不会被浏览操作覆盖。

状态规则：

- `chat`、`information`、`action` 和 `service_error` 不写购物约束；
- 选择请求中出现一个与当前任务不同的明确商品类型时，系统将其视为新任务并原子执行 `replace`；不会要求用户使用“改成”之类的固定措辞。只有同一条消息同时包含多个商品类型时才要求确认。
- `replace` 会清除旧任务的类型、预算、厂商和主题；只有用户明确说“同样预算”等延续语义时才应保留旧条件。
- 失败轮次会回滚该轮已写入的 `constraint_update`；
- 模型规划上下文会排除历史 `service_error` 文案，避免一次网络故障影响后续问候或闲聊；
- 目录查询可以读取当前消息中的过滤条件，但不会覆盖未完成推荐的条件。最近一次成功目录查询另存为 `last_catalog_context`；“价位呢？”等省略范围的只读追问可继承它，且不会污染购物任务。

## 5. 目录对齐与推荐

目录规范值保持英文。模型可使用受控别名，例如 `马克杯 → mug`、`衬衫/T恤 → shirt`、`海洋主题 → Ocean`；别名仅在对应英文值存在于当前目录时生效，用户原话始终保留在 `raw_value`。不能把未知品牌或风格擅自映射到目录标签。

主题/风格的强度取决于它在句子中的角色，而不是是否出现“必须”二字：直接修饰待购商品的主条件（如“纽约风的衣服”“Beach themed mug”）是硬条件；“喜欢”“优先”等偏好从句才是软偏好。模型可把未收录中文表达映射为真实英文目录值；只要它保留原始短语并给出目录提示，代码会按主条件位置提升其强度，不必为每个中文标签手写映射。

目录查询的每个主题/风格映射都会记录解析状态：一个规范值为 `resolved`；零个为 `unresolved`；多个为 `ambiguous`。未知词不会被当作“零结果”的硬筛选，而会保留已知条件并展示可验证标签；一词多义也不会把多个候选标签错误地按 AND 同时过滤，而会展示这些候选标签供用户选择。推荐资格是独立的 `RecommendationPolicy`：默认必须已知商品类型，且还要有预算、品牌或主题等至少一项可比较条件；仅有类型时统一追问，不允许模型直接推荐最低价商品。此策略是可配置的产品规则，不是模型能力假设。

未映射的软偏好可以用于候选解释，但不得被表述为已验证满足；确定性排序会将其标为 `closest_alternative` 并补充未验证提示。

Python 先执行类别、预算、明确主题、硬厂商等硬约束过滤；风格、用途、优先厂商等软偏好仅参与排序。排序策略固定为：**已验证偏好（优先厂商、命中偏好标签数）→ 价格从低到高 → 商品 ID**。所有候选和排序依据都会进入 trace，页面仅展示前 `AGENT_MAX_CANDIDATES` 个候选以便比较。

因此正常聊天、目录查询和推荐都只需一次模型调用（计划）。模型仍承担语义理解、主任务/追问判断与目录映射；工具负责可验证的检索与决策执行。

## 6. 故障与可观测性

模型异常被归类为 `configuration`、`connection`、`timeout`、`authentication`、`rate_limit`、`provider_status`、`model_request_error` 或 `invalid_model_output`。结果 trace 记录失败阶段和分类；页面显示脱敏的阶段/类别，不显示 API Key 或完整供应商响应。

对 `invalid_model_output`，在没有读取目录或写入状态前，系统允许一次仅针对协议格式的 API 修复调用：将校验错误与原计划交给模型，要求重写为合法 `TurnPlan`。修复成功后继续执行；仍失败时才返回“模型回复未通过工作流协议校验”。这不是本地语义兜底，连接、超时和认证错误也不会触发此修复。

API 客户端默认超时 45 秒，并允许至多一次可配置重试（`DEEPSEEK_MAX_RETRIES=1`，范围 0–2）。重试只针对客户端/服务端传输层；不会以本地推荐替代失败的模型语义判断。

## 7. 测试原则

测试案例是回归样本，不是生产规则。除具体中英文样例外，测试还验证以下性质：目录只读查询不改变购物状态；失败轮次不遗留约束；聚合字段必须来自真实目录；服务错误不进入后续模型上下文；计划必须通过 schema 组合校验；类型不足的选择计划必须被策略追问；交易请求不得被误执行为详情；未知和歧义目录词必须保留解析状态。真实 API 冒烟测试用于验证网络、代理、模型名和实际 JSON 遵从性，不能由 Stub 测试替代。

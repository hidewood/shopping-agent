# 实验报告：可靠购物 Agent —— 语义编译器、状态机与提示词工程

## Summary

本报告记录一个面向固定本地目录（`products.jsonl`，1,740 件：`mug` 870 / `shirt` 870）的中英文购物 Agent 的逻辑设计。核心主张是：**可靠与「智能感」来自工作流、状态机、提示词工程与确定性执行的分层设计，而非堆叠 RAG、ReAct 或多 Agent。**

系统把「自然语言 → 动作」拆成四层：① DeepSeek 每轮只输出一个有序、受限的 `TurnProgram`（≤8 个子句，带证据与依赖关系）；② Python 校验后把语义编译为唯一的 `PurchasePlan`（显式状态机）；③ 确定性执行目录接地、硬过滤、组合求解；④ 账户副作用与会话状态在同一事务原子提交、按消息 ID 幂等。模型从不生成商品 ID、内部条目 ID 或数据库操作，也从不拥有状态。

实测（`agent-v2`，DeepSeek V4 Pro，关思考模式）：71 条结构化样例（15 个语义维度）+ 40 条边界扫描 + 10 组多轮对话 + 账户联动/HTTP 契约/持久化，共约 176 个测试点；修复 14 处「模型输出 vs Python 编译」的接地边界缺陷；单轮时延约 1.5–2.3 秒。

## 1. 问题定义与设计哲学

### 1.1 固定小目录不等于可靠对话

早期实现（单意图 `TurnPlan` + 事件溯源 + 前置正则）暴露四类结构性缺陷：

1. **单意图上限**：旧 `TurnPlan` 每轮只能表达 `goal=chat|information|selection|action` 五选一，无法在单轮完成「比较 P0005 和 P0011，再把便宜的加购物车」这类复合请求。
2. **复杂句绕过模型**：前置正则直接回复若干分支，复杂句可能命中错误分支，绕过大模型的语义理解。
3. **只读查询被误当澄清答案**：澄清过程中插入「你们有多少种主题」这类只读问题，可能被当成对澄清的回答。
4. **副作用与状态分离**：收藏/购物车/订单副作用与状态更新分离，双击或重试产生重复副作用。

### 1.2 为什么不加 RAG / ReAct / 多 Agent

数据规模（1,740 件、两个互斥类型）与任务形态（单轮封闭的「需求理解 → 检索 → 过滤 → 排序」链路）决定了：**检索不是瓶颈，理解与状态一致性才是**。多步推理循环（ReAct）、向量检索（RAG）、多 Agent 编排在此只增加时延、复杂度与失败面，不转化为购物效果。因此方向是**做减法**——把模型职责缩小到「语义编译」，把状态收敛到「单一聚合 + 单一事务」。

## 2. 工作流：语义程序（TurnProgram）

### 2.1 从「单意图分类」到「语义程序」

旧设计让模型输出一个扁平对象（`goal/target/requirement/action`），本质是「分类器」：每轮五选一。新设计让模型输出一个**有序语义程序**（`TurnProgram`），本质是「编译器前端」——模型只负责把自然语言翻译成受限的指令序列，Python 负责执行。

![图 1：工作流流水线——从用户消息到原子提交的完整链路](images/a.png)

### 2.2 TurnProgram 的结构

`TurnProgram` 的根键固定为 `schema_version`、`primary_act`、`clauses`、`relations`：

- **`clauses`**：有序数组（≤8 个），15 种子句——`chat / catalog_query / purchase_set / line_update / line_remove / budget_set / budget_clear / recommend / plan_query / product_query / capability_query / favorite_add / cart_add / order_create / order_cancel`。每个子句有 `kind`（类型）、`evidence`（证据）、`payload`（载荷，每种 kind 有严格的字段契约）。
- **`relations`**：子句间依赖，仅四种——`before`（顺序）、`result_reference`（引用前序结果）、`conditional_non_empty` / `conditional_empty`（条件门控）。
- **`evidence`**：任何状态变更或账户动作子句，必须携带用户原话的**精确子串**作为证据。

这三点让「复合意图」成为可能并可控：

- 「比较 A/B 再买便宜的」= `product_query` + `cart_add`（后者 `result_reference` 前者）——一条消息多个动作，Python 按序编译执行。
- 「找到 0 个就不再推荐」= 用 `conditional_non_empty` 门控，避免对空结果继续推理。
- **证据约束是反幻觉的硬闸门**：模型不能凭空「帮你下单」，因为下单子句的 `evidence` 必须是用户消息里真实出现过的子串，否则直接判 `invalid_model_output`。

### 2.3 编译-执行分离

**模型只「翻译」，Python 只「执行」。** `_interpret`（模型 → TurnProgram）与 `_compile_and_execute`（TurnProgram → 结果 + 新聚合）严格分开，中间无模型参与。`_compile_and_execute` 按子句顺序依次执行，维护一个 `proposed` 聚合（deepcopy 的临时状态），每个子句只产生确定性副作用；`mutation` 计数与 `finalize_plan_version` 保证只有真正变更计划时才推进 `plan_version`。

## 3. 状态机：ConversationAggregate 与 PurchasePlan

### 3.1 聚合结构

旧设计用「事件溯源 + reducer 重放」派生当前需求，对「追加预算/换类型」正确，但对「澄清中途插入只读查询」「取消某项」这类**中断语义**表达困难。新设计改为**显式聚合对象**（`ConversationAggregate`）：

| 字段 | 含义 |
| --- | --- |
| `active_plan` | 唯一活动采购计划（`PurchasePlan`），含多条 `line` |
| `pending_change_set` | 澄清中挂起的未完成变更 |
| `pending_clarification` | 待澄清问题（含 `interruption_policy`） |
| `current_result_snapshot` | 版本化推荐结果快照 |
| `last_catalog_context` | 最近目录查询上下文 |
| `catalog_version` | 目录内容哈希 |

`PurchasePlan` 的每一条 `line` 自包含 `item_type / units / recipient / manufacturer / constraints / unit_budget / candidate_count / fulfillment_mode`。**单件是只有一条 line 的计划**——多人、多类、无偏好走同一条执行链，没有特殊工作流。

### 3.2 状态转移

![图 2：PurchasePlan 状态机——状态转移、中断与重入](images/b.png)

### 3.3 中断、重入与版本化

1. **澄清的中断语义**（`Draft --> Draft`）：澄清中插入只读查询，不消费、不覆盖待澄清问题，`interruption_policy="preserve_on_read_only"` 保证进行中的选购不被读操作破坏。
2. **澄清的重入**（`Draft --> Ready`）：用户回答缺失字段时，模型**重建挂起的 `purchase_set` / `line_update`**，而不是当成全新请求——这是「多轮连续」与「每次重来」的本质区别。
3. **版本化快照**：每次变更递增 `plan_version`；快照同时记录 `catalog_version`，目录一旦被管理员修改，旧快照立即失效，下一轮重新检索，杜绝「旧推荐当新事实」。

## 4. 提示词设计（关键）

提示词是把「模型该做什么」约束清楚的关键。系统用了两个提示词，分别对应「理解」与「表达」两端，中间隔着一层确定性执行——**模型不直接产出最终答复，也看不到内部状态**。

![图 3：提示词分层——编译前端、确定性执行与接地生成](images/c.png)

### 4.1 编译前端：TURN_PROGRAM_SYSTEM

这是把自然语言翻译成 `TurnProgram` 的系统提示词，核心是**用 schema + 规则双重约束模型输出**：

**（a）schema 约束**：提示词明确列出根键、15 种子句、每种子句的 payload 字段、以及关系类型；`_PAYLOAD_CONTRACTS` 用 JSON Schema 逐字校验，未知字段直接 `invalid_model_output`。这让模型只能输出「可校验的受限指令」，而非自由文本。

**（b）证据要求**：状态变更/账户动作子句必须携带用户原话的精确子串（`evidence`），否则拒绝——从源头杜绝模型「自作主张下单」。

**（c）never guess 原则**：未知主题保留 `raw_value`、`catalog_values` 留空，**绝不映射到近似标签**（「动画风」不能悄悄变成 Nature）。Python 侧 `_ground_constraints` 只从别名表 + 名称/描述做确定性匹配，命中不了就进入澄清。

**（d）语义规则**（`Important semantics` 一节），把实测中反复出现的歧义写成显式规则：

| 规则 | 例子 |
| --- | --- |
| 货币：裸数字=USD，明确「元/¥」=CNY | 「预算20以内」=USD；「预算20元以内」=CNY 澄清 |
| 「推荐N款」=候选数，「买N件」=数量 | 「推荐三款」`candidate_count=3`；「买三件」`units=3` |
| 「支付/付款」→ capability_query，绝不 order_create | 「支付P0005」→「不支持真实支付」 |
| 「送礼/送人的」→ recipient=null，不用占位名 | 「送人的」不写成 `someone` |
| 「换一批」→ recommend 带 `exclude_shown` | 排除已展示商品、取下一批 |
| 带类型+预算即使无购买动词也是选购 | 「马克杯预算20以内」= purchase_set，不是 budget_set |
| 多类型逐项发 group，不省略未知类型 | 「马克杯和T恤和帽子」→ 帽子也发出来让代码澄清 |
| 数量 1–20 整数；负数/小数/超限保留原文澄清 | 「-5个」不擅自改成 1 |
| 总价 vs 总预算 | 「一共多少钱」= plan_query；「总预算」= budget_set |
| 偏好 vs 硬约束 | 「喜欢/最好」= preference；「必须/只要」= hard |

这些规则不是「提示词越聪明越好」，而是**把模型在边界上的自由裁量权收回到确定性代码里**——每一轮修复都在这一层加一条规则，而不是在 Python 里加硬编码分支。

### 4.2 接地生成：GROUNDED_SUMMARY_SYSTEM

这是「表达」端的提示词：确定性执行结束后，把**接地后的事实**（商品名、标签、价格、约束、放宽条件）喂给模型，让它生成自然语言回复。铁律：

- **只使用给定事实**，绝不编造目录里没有的商品、价格、库存、物流、支付。
- **区分 `selected`（主推荐）与 `options`（备选）**：主推荐重点解释「为什么推荐它」，备选最多一句话带过，不把备选当新推荐。
- 无匹配时诚实说明 + 顺带提一句放宽后的最近选择（若事实里有）。

这一层让回复从「`mug选中 P0005 ×1。合计 $9.99`」的机械模板，升级成「这款 Rustic Ocean Mug 带有海洋主题，价格 9.99，在您的预算内」的自然语言——**但事实仍然来自 Python，模型只能组织语言、不能编造。**

## 5. 逻辑设计原则

1. **模型只编译，不拥有状态**：模型看到的是编译前端，看不到、也改不了内部 `line_id` 或数据库。状态迁移全部由 Python 完成，可审计、可重放。

2. **确定性接地，never guess**：`_ground_constraints` 从用户原文 + 别名表 + 名称/描述推导规范值，绝不信任模型给的 `catalog_hint`。未知主题进入澄清，不做近似映射——错误过滤比少召回更糟。

3. **检索优先于追问**：只要商品类型已知（`mug` 与 `shirt` 互斥，类型是唯一真正阻塞的缺口），就检索真实商品并报告如何收窄，而不是先追问预算或主题。

4. **能力注册表**：`capability` 显式声明 `payment/inventory/shipping/returns/external_info` 为假、`catalog/recommendation/favorite/cart/order` 为真。通用问询用正面语气，具体问询才诚实拒绝。

5. **确定性组合求解**：多条目在合计预算下的选择用有界深度优先搜索（visited 上限 100,000），保持目录偏好序，避免笛卡尔积。

## 6. 事务与幂等：副作用正确性

账户动作（收藏/加购/下单/取消）不是即时写库，而是编译为 `effects` 列表返回给 API 层，在**同一个 SQLite 事务**里与会话状态一起提交：

- `revision` 乐观锁（`WHERE revision = ?`）检测并发冲突，冲突返回 `conflict` 由前端重放；
- `message_id` 幂等去重，同一消息重试不会重复收藏、加购或下单；
- `BEGIN IMMEDIATE` 保证「购物车清空 + 订单创建」要么同时成功、要么同时回滚。

账户的「读」与「写」对称：对话能下单，也能回答「我有哪些订单」「我的收藏」「购物车有什么」——通过 `order_query / favorite_list / cart_query` 三个只读子句 + 一个 `store_query` 回调（API 层注入）查 SQLite。

## 7. 实验设置

| 项目 | 取值 |
| --- | --- |
| 模型 | `deepseek-v4-pro` |
| 思考模式 | `DEEPSEEK_THINKING_ENABLED=false` |
| 采样 | `temperature=0.1`、`max_tokens=4096` |
| 超时/重试 | `DEEPSEEK_TIMEOUT_SECONDS=30`、`DEEPSEEK_MAX_RETRIES=0` |
| 目录 | `products.jsonl` 1,740 件；353 个标签，44 个有中文别名 |
| 代码版本 | 分支 `agent-v2`（V3 语义编译器） |
| 前端 | Vue 3 + TypeScript；32 秒停止等待，重试复用消息 ID |

## 8. 验证方法与指标

验证按**语义维度**组织，而非固定句式：意图路由、接地正确性、状态正确性、约束正确性、副作用正确性、时延。失败按 `model / protocol / state / catalog / transaction / UI` 六类归因。测试分层：

- **L1 对话**：71 条 A-O 矩阵 + 40 条边界扫描 + 10 组多轮对话（真实 DeepSeek）。
- **L2 账户+持久化**：写读全链路、幂等/乐观并发、会话存/取/删（真实 SQLite）。
- **L3 HTTP 端点**：认证/购物车/订单/收藏/管理员/会话/商品/健康（TestClient，含 401/403/JWT）。
- **L4 管理员**：商品 CRUD、订单状态流转。

## 9. 结果

### 9.1 逐维度验证

能力边界、商品详情/比较（含复合子句）、偏好 vs 硬约束、多轮状态、组合方案、账户副作用（写+读）、语义归一化（小清新→Nature、海边→Beach、星空→Space、山野→Mountain）、计划查询、中英文混合、无结果+放宽——全部通过。

### 9.2 实测发现并修复的缺陷（14 处，全部位于「模型输出 vs Python 编译」边界）

| # | 缺陷 | 修复 |
| --- | --- | --- |
| 1 | 「预算20以内」被判 CNY 拒绝 | 从 evidence 确定性推断货币 |
| 2 | 「都出售什么商品」→「没有找到」 | 补 `list` 文案 + 类型概览 |
| 3 | 「你能做什么」→ 免责声明堆砌 | 能力介绍改正面引导 |
| 4 | 空消息 → service_error | 返回澄清而非抛异常 |
| 5 | 「推荐三款」→ 澄清 | prompt 明确「推荐N款=选购」 |
| 6 | 小清新/海边等不识别 | 别名补到 44 个 |
| 7 | 「送人的」被当成名字 `someone` | prompt 明确送礼=recipient null |
| 8 | 「一共有多少种主题」→ 类型概览 | 收窄类型概览触发条件 |
| 9 | 组合后「下单」无法解析 | 裸「下单」默认解析整个快照 |
| 10 | 商品名里的主题词匹配不到（Strawberry→Strawberries） | 单复数归一化 + 名称/描述匹配 |
| 11 | 对话只能写账户、不能读账户 | 新增 order_query/favorite_list/cart_query 读子句 |
| 12 | 「再推荐点别的」不变商品 | `recommend` 加 `exclude_shown` |
| 13 | 支付/付款被当成本地订单 | prompt + 代码兜底，拒绝为 capability |
| 14 | 接地生成的回复太机械 | 加 GROUNDED_SUMMARY_SYSTEM 自然语言生成层 |

### 9.3 已知局限（2 处）

- 小数数量（「1.5个」）、超长主题词——模型解析层直接忽略，prompt 规则拉不回，真实用户几乎不会触发。

### 9.4 时延

关思考模式后，单轮端到端（一次编译 + 一次接地生成）约 **1.5–2.3 秒**。

## 10. 分析

**观察**：14 处缺陷全部落在「模型输出 vs Python 编译」的边界——模型要么发错子句类型、要么发错字段值（货币、收礼人）、要么漏发子句（`purchase_set`）。

**解读**：工作流、状态机、事务三层是可靠的，脆弱点集中在**语义接地**这一层。这恰好印证了设计哲学——把模型约束在「可校验、带证据」的编译前端，把波动挡在事实之外。修复策略因此是「能接地就接地」（货币、别名、名称匹配、收礼人），而非「让模型更聪明」。

## 11. 结论

可靠购物 Agent 的智能感来自**工作流、状态机、提示词工程与确定性执行的分层设计**：`TurnProgram` 语义程序把模型约束在「可校验、带证据、≤8 子句」的编译前端；`ConversationAggregate` 用显式状态机承载中断与重入语义；两个提示词分别把「理解」与「表达」的边界划清；Python 确定性执行接地、过滤、组合求解；账户副作用与状态在同一事务原子提交、幂等。

## 12. 局限与注意事项

- 语义编译仍受模型版本与网络波动影响；一次修复不保证所有异常输出恢复。
- 目录只含 `mug` / `shirt`；人民币预算不自动换算。
- 别名表覆盖 44 / 353 个标签（约 12%），模糊主题覆盖仍有限。
- 最多 8 个语义子句、每条 1–20 件；组合搜索 100,000 节点上限。
- 真实支付、库存、物流、退换货未接入，回复由能力策略约束。

## 13. 下一步

1. **别名覆盖**：继续扩充 `catalog_language.json` 标签别名，提升模糊主题召回。
2. **有界语义归一化**（可选 V4）：对别名未命中的词，LLM 提议候选、Python 校验，恰好一个命中即用、零个/多个则反问。
3. **离线测试补全**：将新 V3 测试套件补成与旧 180 项对等的覆盖，接入 CI。
4. **并发/重试压测**：对双击下单、网络重试的幂等性做专项压测。
5. **限流**：长期公开部署时，对后端 API 加注册门槛 + 调用频率限制，防止他人消耗 DeepSeek 配额。

## 复现说明

```bash
pip install -r requirements.txt
cp .env.example .env          # 设置 DEEPSEEK_API_KEY，DEEPSEEK_THINKING_ENABLED=false
python -m uvicorn starter.api:app --reload --port 8000
cd frontend && npm install && npm run dev

python tests/_ao_live.py      # 结构化样例（真实 DeepSeek）
python tests/_edge_sweep.py   # 边界扫描
python tests/_backend_suite.py # 账户联动 + API 契约
```

数值与通过率应在固定模型、日期、commit 下重新运行后记录，不应以本报告历史结果替代新一次验收。

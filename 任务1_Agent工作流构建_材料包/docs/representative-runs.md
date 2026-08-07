# 代表性运行记录

测试日期：2026-08-07。商品库：1,740 件商品。

## T01：主题映射与预算

输入：`I need a Clothes themed shirt that costs less than $23.`

- 结果：推荐 `P1635 Small Clothes Shirt`，价格 `$16.99`。
- 核验：商品类别为 shirt，标签包含 Clothes，价格严格低于 `$23`。
- 结论：通过。

## T03：软偏好厂商

输入：`Buy an affordable mug related to Sunny; prefer Bayer-and-Sons if available.`

- 结果：推荐 `P0599 Ergonomic Flowers Mug`，价格 `$10.99`。
- 核验：商品为 mug，标签包含 Sunny，厂商为 Bayer-and-Sons；该厂商存在满足其余条件的商品时被优先选择。
- 结论：通过。

## T04：不存在的硬类别

输入：`I need a camera under $100.`

- 结果：不购买，返回“商品类别 camera 无法映射到商品库类别”。
- 核验：没有把 Camera 标签的 shirt 错误当作相机推荐。
- 结论：通过。

## T05：官方品牌/IP 硬约束

输入：`I must buy an official Disney shirt under $30.`

- 结果：不购买，页面说明商品库无法满足 Disney 条件。
- 核验：没有将普通 shirt 或风格相近商品伪装成 Disney 正版。
- 结论：通过。

## T07：不完整价格

输入：`I need a shirt that costs less than .`

- 结果：请求补充金额，不进入检索和购买。
- 结论：通过。

## T08：信息不足的主动追问

输入：`I want the best product.`

- 结果：页面追问商品类型、风格、主题或预算。
- 结论：通过；后续回答由多轮状态管理继续处理。

## M01：追问后的多轮补充

第 ① 轮输入：`I want a gift.`

- 结果：返回 `clarification`，询问商品类型是 mug 还是 shirt；未进入购买。

第 ② 轮输入：`A mug under $30; prefer Ocean themed products.`

- 结果：推荐 `P0005 Rustic Ocean Mug`，价格 `$9.99`。
- 状态核验：事件日志先记录用户消息；第 ② 轮记录商品类型 mug、严格预算 `< $30`、Ocean 软偏好，再归约为当前需求。
- 商品核验：商品类别为 mug、价格严格低于 `$30`，且标签包含 Ocean。
- 结论：通过。

## M02：预算替换

第 ① 轮输入：`I need a mug under $30.` ；第 ② 轮输入：`Actually, under $8.`

- 结果：第 ② 轮返回 `no_match`。
- 状态核验：当前预算被替换为 `< $8`，而不是同时保留 `< $30` 和 `< $8`；商品类型 mug 继续有效。
- 结论：通过。

## M03：同轮商品类型冲突

输入：`I need a mug and a shirt.`

- 结果：返回 `conflict`，要求用户在 mug 与 shirt 中选择一类；没有推荐商品。
- 结论：通过。

## M04：显式换类

依次输入：`I need a mug under $30.` → `I need a shirt.` → `Actually, change to a shirt under $30.`

- 结果：第 ② 轮不静默换类并要求确认；第 ③ 轮接受明确修改，使用 shirt 与 `< $30` 重新检索并给出推荐。
- 状态核验：最终归约需求的商品类型为 shirt。
- 结论：通过。

## 统一计划与双语状态回归（API Stub，2026-08-08）

以下为不使用真实 API Key 的结构化 API Stub 回归，用于核验工作流编排；网页仍需按 `test-cases.md` 进行真实 DeepSeek 人工复测。

### R01：通用目录事实

输入：`你家都有什么商品？`

- `TurnPlan.intent`：`catalog`；操作：`count + group_by_item_type`。
- 结果：返回类别统计为 mug 870 件、shirt 870 件。
- 核验：没有进入需求追问或推荐。

### R02：短句存在性查询

输入：`T恤有吗？`

- `TurnPlan.intent`：`catalog`；操作包含 `count`。
- 结果：返回本地目录中的 shirt 数量 870。
- 核验：没有因缺少预算而进入 `recommendation`。

### R03：中英同义类别连续输入

依次输入：`我想买 T恤，预算低于 $30` → `我想买 shirt，预算低于 $30`

- 结果：第二轮继续推荐流程，不显示“改成 shirt”的冲突提示。
- 状态核验：两轮的 canonical item type 均为 `shirt`；原始表达仅用于面向用户的文字。
- 结论：通过。

### R04：模型 schema 与决策输出保护

- 当目录查询模型返回 `concepts: null` 时，系统将其规范为空数组并继续执行目录查询，不再抛出 `TypeError`。
- 当候选决策模型返回目录外商品 ID 时，系统返回 `service_error`，保留失败阶段 trace，并回滚本轮未完成的 `constraint_update`；不会改为推荐本地排序第一名。
- “有衬衫吗？都有哪些风格？”规划为 `count + group_by_tag`，标签统计必须来自真实 shirt 商品；目录查询不覆盖待补充的推荐状态。

## 已知边界

- 系统只使用 DeepSeek API；若请求超时、配置缺失或 JSON 无效，会显示模型服务错误，不会以本地规则生成推荐。
- 对 `Disney-style` 这类无法映射到目录标签的软风格偏好，模型只能给出相近方案并明确其不是已验证的 Disney 商品。
- 目录事实问题由统一 TurnPlan 的受限操作执行；网页人工测试仍应确认真实模型对短句、复合问题和多轮上下文的计划稳定性。

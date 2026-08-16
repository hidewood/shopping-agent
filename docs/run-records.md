# 代表性运行记录

本文展示购物 Agent 的若干代表性运行，每个示例按「用户需求 → 关键处理过程 → 最终结果」组织。运行环境：`deepseek-v4-pro`，关闭思考模式，`temperature=0.1`。

## 示例 1：需求理解 + 语义归一化 + 推荐 + 结果说明

**用户需求**：我想给女朋友买个小清新的马克杯，预算20以内

**关键处理过程**：

1. **语义编译**：模型把这句话编译为 `TurnProgram`，含两个子句——`purchase_set`（建立采购计划：类型 `mug`、主题「小清新」、预算 $20）和 `recommend`（推荐）。
2. **语义归一化**：「小清新」被确定性接地到目录标签 `Nature`（通过别名表），Python 只信任接地后的 `Nature`，不信任模型的原始文本。
3. **确定性排序**：在 `mug` 且含 `Nature` 标签、价格 ≤$20 的商品中，按「偏好 → 价格 → 商品 ID」确定性排序，选出候选。

**最终结果**：

- 推荐商品：`Licensed Tree Mug`（$9.99，标签 `Tree / Nature / Juniper`）
- 回复：*「这款 Licensed Tree Mug 很适合小清新风格，有树木和自然元素，价格 9.99 美元也在预算内。另外还有 Tasty Night Mug 和 Gorgeous Rocks Mug 可选。」*

> 注意：回复里的「树木和自然元素」「价格 9.99」都是 Python 接地后喂给模型的**事实**，模型只负责组织语言，不能编造。

## 示例 2：商品搜索（目录查询）

**用户需求**：衬衫有什么价位？

**关键处理过程**：

- 语义编译为 `TurnProgram`：子句 `catalog_query`（`item_type=shirt`，操作 `price_range`）。
- Python 检索所有 `shirt` 商品，计算价格区间（不调用第二次模型）。

**最终结果**：

- 回复：*「衬衫的价格在 $9.99 到 $20.99 之间。」*

## 示例 3：候选比较

**用户需求**：比较 P0005 和 P0011

**关键处理过程**：

- 语义编译为 `TurnProgram`：子句 `product_query`（`query=compare`，`product_ids=[P0005, P0011]`）。
- Python 从目录精确查回两件商品的名称、价格、标签，并排对比。

**最终结果**：

- 回复：*「Rustic Ocean Mug（P0005）和 Gorgeous Water Mug（P0011）都是 $9.99 的马克杯，前者是 Sunset、Beach、Ocean 主题，后者是 Fog、Lake、Water 主题，看您更喜欢哪种风格。」*

## 示例 4：复合请求（比较 + 加购）

**用户需求**：比较 P0005 和 P0011，把便宜的加购物车

**关键处理过程**：

- 语义编译为 `TurnProgram`，含**两个有序子句**：`product_query`（比较）→ `cart_add`（加购，通过 `result_reference` 引用前序结果）。
- 这一条消息被编译为「先比较、再执行」的语义程序，Python 按序执行。

**最终结果**：

- 响应类型：`action`（产生了 `cart.add` effect）
- 回复：*「对比结果：Rustic Ocean Mug（P0005）$9.99…；已加入购物车：P0005。」*

## 示例 5：多轮状态（换类型）

**用户需求**：① 我想买马克杯 → ② 换成T恤

**关键处理过程**：

1. 第 1 轮：`purchase_set` 建立 `mug` 采购计划。
2. 第 2 轮：语义编译为 `purchase_set`（`mode=replace`，类型改为 `shirt`），计划归约后 line 从 `mug` 替换为 `shirt`（历史类型不残留）。
3. 确定性推荐选出 `shirt` 候选。

**最终结果**：

- 推荐商品：`Fantastic Secluded Shirt`（$9.99，标签 `House / Trees / Fog / Secluded`）
- 回复：*「为你选了这件 Fantastic Secluded Shirt，图案是幽静的树林和雾景，很有氛围感，价格也只要 $9.99。另外还有沙漠和建筑图案的衬衫可选。」*

## 示例 6：约束检查 + 无结果处理

**用户需求**：Ocean主题马克杯，预算5以内

**关键处理过程**：

- `purchase_set` 建立采购计划：`mug` + `Ocean` 硬约束 + 预算 $5。
- 硬过滤：目录中 `Ocean` 标签的马克杯最低 $9.99，全部超过 $5 预算。
- 结果：`no_match`，同时给出「放宽一个约束后的最近方案」。

**最终结果**：

- 回复：*「没有找到满足全部条件的Ocean主题马克杯。如果预算能放宽一点，有一款 Rustic Ocean Mug 比较接近，可以看看。」*

## 示例 7：账户动作（收藏）

**用户需求**：收藏 P0005

**关键处理过程**：

- 语义编译为 `TurnProgram`：子句 `favorite_add`（`product_ids=[P0005]`，`confirmed=true`）。
- 编译为 `favorite.add` effect，由 API 层在 SQLite 事务中原子落库（`message_id` 幂等，重试不重复收藏）。

**最终结果**：

- 响应类型：`action`
- 回复：*「已收藏 P0005。」*

---

以上 7 个示例覆盖题目的主干功能：需求理解（示例 1）、商品搜索（示例 2）、候选比较（示例 3）、约束检查（示例 6）、购买决策（示例 1/5）、结果说明（示例 1）；以及拓展功能：复合意图（示例 4）、多轮状态（示例 5）、无结果处理（示例 6）、账户动作（示例 7）。系统更完整的评测见 [实验报告](experiment-report.md) 与 [测试验收手册](test-report.md)。

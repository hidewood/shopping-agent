# Shopping Agent 测试场景与人工核验

## 使用方式

启动 `streamlit run app.py` 后，在“智能推荐”页依次输入下列任务。推荐结果可在“查看推荐理由、备选与核验依据”中确认筛选事实；追问和冲突则直接在聊天记录中显示，继续在页面底部输入框回答即可。

真实 API 冒烟测试默认不会运行；在 API Key、网络与代理均准备好后，可设置 `RUN_LIVE_API_TESTS=1` 并执行 `python -m unittest tests.test_live_api_smoke -v`。该测试会产生少量 API 调用，用于验证 Stub 无法覆盖的真实服务行为。

每次测试均检查四项：

1. `turn_planning` 的 `goal`、`target`、目录操作、状态动作和（选择时）`selection_mode` 是否符合本轮目的；
2. `catalog_query_grounding` 或 `catalog_grounding` 是否将用户表达映射为目录中的合法值；
3. 目录聚合/`retrieval_and_hard_filtering` 的数量与真实数据是否一致；
4. `purchased_product_id`、`summary`、`candidate_comparison` 的排序依据和 `decision_validation` 是否相互一致。

## 场景清单

| 编号 | 用户输入 | 目的 | 预期行为 |
| --- | --- | --- | --- |
| T01 | `I need a Clothes themed shirt that costs less than $23.` | 验证主题目录对齐 | `Clothes themed` 对齐至 `Clothes`；最终商品必须为价格低于 $23 的 Clothes 标签 shirt。 |
| T02 | `Find a shirt about Barn from Konopelski-Inc with price under $17.` | 验证类别、厂商、价格和标签硬过滤 | 最终商品必须同时满足类别、厂商、预算与 Barn 标签条件。 |
| T03 | `Buy an affordable mug related to Sunny; prefer Bayer-and-Sons if available.` | 验证“优先厂商”不是硬约束 | 厂商显示为偏好；若存在符合其余条件的 Bayer-and-Sons 商品则优先选它。 |
| T04 | `I need a camera under $100.` | 验证不存在的硬类别 | 类别无法对齐时返回 `purchased_product_id: null`，不得将 camera 替换为 mug 或 shirt。 |
| T05 | `I must buy an official Disney shirt under $30.` | 验证硬品牌/IP 不做语义替代 | 无合法 Disney 对齐值时返回无匹配；不得将普通卡通风商品说成满足要求。 |
| T06 | `I want a Disney-style shirt under $30.` | 验证未映射风格不被伪称满足 | 可在已满足硬条件的商品中推荐，但必须标记为 `closest_alternative`，说明 Disney-style 没有可验证目录映射，未将其当作匹配事实。 |
| T07 | `I need a shirt that costs less than .` | 验证输入完整性 | 识别缺少金额并提示重新输入；不得将其按无预算处理。 |
| T08 | `I want the best product.` | 验证信息不足 | 返回追问，例如询问商品类型、预算或用途；不应直接购买。 |
| T09 | `你好` | 验证聊天计划 | `goal=chat,target=none`，自然问候；不得进入商品检索或改变购物条件。 |
| T10 | `我想买一件T恤` | 验证推荐资格策略 | 即使模型给出 `goal=selection,target=catalog`，执行前仍追问预算、主题或品牌；不得直接推荐最低价商品。 |
| T11 | `你家都有什么价位的衬衫` | 验证价格范围查询 | `goal=information,target=catalog`、操作为 `price_range`；返回 shirt 的实际商品数和最低至最高价格，不得追问预算或推荐商品。 |
| T12 | `当前商品库有哪些商品类型？` | 验证目录概览查询 | `goal=information,target=catalog`、操作含 `group_by_item_type`；返回 mug 与 shirt 的真实数量。 |
| T13 | `请介绍 P0005 的描述和标签` | 验证商品详情查询 | `goal=information,target=product`；返回该 ID 的价格、厂商、标签和描述，不推荐或替换为其他 ID。 |
| T14 | `比较 P0005 和 P0006` | 验证商品比较查询 | `goal=information,target=product`；并列展示两个真实商品的字段；不存在的 ID 必须明确报错。 |
| T15 | `我想买一个海洋主题的马克杯，预算低于 30` | 验证中英目录对齐 | `马克杯 → mug`、`海洋主题 → Ocean`；推荐商品必须是低于 30 的 Ocean 标签 mug。 |
| T16 | `我想买一个马克杯，预算低于 30，优先海洋主题` | 验证中文软偏好 | `海洋主题 → Ocean` 但保持软偏好；不能作为硬过滤条件。 |
| T17 | `你家都有什么商品？` | 验证通用目录事实计划 | `goal=information,target=catalog`，操作含 `count + group_by_item_type`，页面显示 mug 870 件、shirt 870 件；不得追问预算。 |
| T18 | `T恤有吗？` | 验证短句“是否存在”计划 | `goal=information,target=catalog`，操作含 `count`，回答本地目录存在 shirt，并显示数量 870；不得进入选择流程。 |
| T19 | 任意购物请求，但临时移除 API Key 或断开 API | 验证 API-only 失败处理 | 返回 `response_type: service_error` 和模型服务错误说明；不得显示本地规则推荐或商品卡片。 |
| T20 | 目录查询解析器返回 `concepts: null` | 验证模型 schema 容错 | 将 `null` 规范为空数组，正常完成目录查询；页面不得显示 Python traceback。 |
| T21 | ① `我想买 T恤，优先海洋主题` ② `必须有 Ocean 标签` | 验证软偏好升级为硬约束 | 第 ② 轮的 Ocean 必须进入 `required_tags`，不再只是 `preferred_tags`。 |
| T22 | `我想买一件 T恤` → `卡通风` | 验证风格语义 | 在没有“必须/只要/一定要”时，`卡通风` 作为软偏好处理；无法映射时可说明无法验证，不得伪装为硬条件无匹配。 |
| T23 | `你家有衬衫出售吗？都有哪些风格的衬衫呢？` | 验证复合目录操作 | `goal=information,target=catalog`，操作为 `count + group_by_tag`；返回 shirt 总数与来自真实目录的标签/风格统计，不改写待补充的推荐状态。 |
| T24 | `下单 P1194` | 验证交易能力边界 | `goal=action,target=transaction,action=order.create`；返回 `capability_unavailable`，明确目前不支持下单；不得显示详情、推荐或声称已创建订单。 |
| T25 | `有卡通风格的衬衫吗？` | 验证未知目录词 | 若“卡通风格”无受控映射，则 trace 标为 `unresolved`；说明不能验证其对应标签，同时展示 shirt 范围内真实风格标签，不得把它说成“0 件”。 |
| T26 | `有自然相关的衬衫吗？` | 验证多义目录词 | 若词语映射到多个标签，则 trace 标为 `ambiguous`；不得把多个标签按 AND 过滤为零结果，而应展示候选标签并请用户选择。 |
| T27 | `我想买一件衬衫，不限预算和风格` | 验证显式开放选择 | 计划可用 `selection_mode=explicitly_open`；在产品策略允许时可进入推荐，且推荐理由应说明用户已明确不限制条件。 |

## 多轮扩展场景

每一组场景开始前点击“新建对话”，随后按顺序发送同组消息。

| 编号 | 对话消息 | 目的 | 预期行为 |
| --- | --- | --- | --- |
| M01 | ① `I want a gift.` ② `A mug under $30; prefer Ocean themed products.` | 验证主动追问与回答继承 | 第 ① 轮只追问商品类型；第 ② 轮继承同一会话并推荐价格低于 $30 的 mug，Ocean 仅作偏好。 |
| M02 | ① `I need a mug under $30.` ② `Actually, under $8.` | 验证预算替换 | 第 ② 轮以 `< $8` 替换 `< $30`，不沿用旧预算；若无候选，明确报告无匹配。 |
| M03 | `I need a mug and a shirt.` | 验证同轮冲突 | 不推荐任一商品，要求用户从 mug 和 shirt 中选择一种。 |
| M04 | ① `I need a mug under $30.` ② `I need a shirt.` ③ `Actually, change to a shirt under $30.` | 验证需求冲突与显式修改 | 第 ② 轮要求确认，不能静默换类；第 ③ 轮接受显式“change to”，以 shirt 重新检索。 |
| M05 | 新建两个对话；对话 A 输入 `I need a mug under $30.`，对话 B 输入 `I want a gift.` | 验证状态隔离 | 对话 B 仍会追问品类，不能继承对话 A 的 mug 或预算。 |
| M06 | ① `我想买一件T恤` ② `衬衫都有什么价位` ③ `预算低于 $20` | 验证目录查询不覆盖推荐状态 | 第 ② 轮回答真实价格范围，且不清空第 ① 轮待补充条件；第 ③ 轮继续原购物需求。 |
| M07 | ① `我想买 T恤，预算低于 $30` ② `我想买 shirt，预算低于 $30` | 验证双语同义类别不冲突 | 两轮均规范为 `shirt`；第 ② 轮继续推荐流程，不出现“改成 shirt”的确认提示。 |
| M08 | ① `我想买一个马克杯，预算低于 $30` ② 展开推荐依据 | 验证确定性候选排序 | trace 的 `candidate_comparison.handler` 为 `deterministic_ranking`；排序依据为已验证偏好、价格、商品 ID，且本轮只有一次 `turn_planning` API 调用。 |
| M09 | ① 令计划 API 超时 ② `你好` | 验证故障隔离 | 第 ① 轮 trace 显示失败阶段和 `timeout`；第 ② 轮正常问候，回复不得主动提及服务错误。 |
| M10 | ① `我想买一件纽约风的衬衫，预算30元以内` ② `推荐一个马克杯，我喜欢清新风格` | 验证新任务替换 | 第 ② 轮不要求“改成马克杯”；当前类型替换为 mug，旧预算和 New York 条件不泄漏。若“清新风格”无目录映射，必须说明未验证，而不能把旧主题当作新偏好。 |
| M11 | ① `马克杯有哪些风格？` ② `价位都有哪些呢？` | 验证目录上下文 | 第 ② 轮继承最近一次成功的 mug 目录范围，返回 mug 的真实价格区间；两轮均不改写未完成购物任务。 |
| M12 | ① 模拟一次不合规 TurnPlan ② 返回修正后的合法计划 | 验证协议修复 | trace 出现一次 `turn_plan_repair`，目录查询仅在修复成功后执行；若第二次仍不合规，返回协议校验失败且不改变状态。 |
| M13 | ① `我想买一件T恤` ② `衬衫都有什么价位？` ③ `预算低于 $20` | 验证任务状态与需求状态分离 | 第 ② 轮的 `task_state` 显示目录浏览且保留 `selection / collecting`；第 ③ 轮仍能延续第 ① 轮的商品需求。 |

多轮场景额外核验：

1. 聊天记录中的上一轮条件是否被正确继承、替换或移除；
2. `response_type` 是否与界面行为一致：`clarification`、`conflict`、`no_match` 或 `recommendation`；
3. 在推荐详情的结构化记录中，是否可看到 `constraint_update` 与 `state_reduction`；
4. 点击“新建对话”后，旧聊天记录和旧条件是否消失。
5. 每轮结构化记录是否包含一次 `turn_planning`；目录事实问题的操作是否与问题维度一致。

## 逐项核验模板

复制以下模板到实验记录中：

```text
测试编号：T__
输入：
目录对齐：原始表达 → 规范字段/值
硬过滤数量：总数 → 类别 → 厂商 → 价格 → 标签
候选商品 ID：
最终商品 ID：
决策级别：exact_match / closest_alternative / no_match / clarification
是否符合预期：是 / 否
异常或备注：
```

## 建议优先保留的代表性记录

建议将 T01、T03、T04、T05、T08、T17、T18、T23、M01、M02、M04、M07、M09 的页面截图和核验结果整理为最终提交的代表性运行记录。它们覆盖目录映射、偏好排序、无匹配、硬约束保护、统一计划、复合查询、主动追问、状态更新、故障隔离和双语同义类别处理。

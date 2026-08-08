# 智能购物 Agent：交接说明

## 启动与验证

- 项目根目录：`E:\Codes\Project\2026.8\任务1_Agent工作流构建_材料包`
- 页面：`streamlit run app.py`
- 自动测试：`python -m unittest discover -s tests -v`
- 配置：复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`；不得提交或打印真实 Key。

当前回归基线：测试套件共 60 项，其中 **59 项离线回归通过**；另有 1 项真实 API 测试默认跳过。已确认项目 Python 环境中的 Streamlit 为 1.61.1；本次未进行自动浏览器 UI 回归，但已完成 Python 编译和单元测试。

另有 `tests/test_live_api_smoke.py`：默认跳过；设置 `RUN_LIVE_API_TESTS=1` 后才会进行两次真实 API 调用，核验聊天和“衬衫是否存在 + 风格标签”复合目录计划。它用于发现代理、网络、模型名和真实 JSON 输出问题，可能产生费用。

## 当前架构

核心实现是 [`starter/agent_interface.py`](../starter/agent_interface.py)，网页是 [`app.py`](../app.py)。唯一的实际执行入口是 `Agent.run_turn(message, state)`；`Agent.run(instruction)` 仅以新建状态调用该入口，保持任务接口兼容。

```text
用户消息
  → DeepSeek 单次 TurnPlan 规划
  → 契约校验
  ├─ chat / none → 客服回复
  ├─ information / catalog → 代码执行固定目录操作
  ├─ information / product → 校验产品 ID 后返回详情或比较
  ├─ selection / catalog → 推荐资格策略 → 状态归约/过滤 → 确定性候选排序
  └─ action / transaction → 检查能力注册表，未实现时明确说明不能下单/支付
```

这取代并移除了旧的“客服协调器 → 购物任务路由器 → 需求解析器”三段串行模型调用。正常聊天、目录查询、详情、比较和推荐均为一次 API 调用；推荐的最终排序由目录工具完成。

## TurnPlan 与目录操作

模型只能输出受限的 `goal + target`、目录操作、目录约束和状态动作，代码验证所有组合。`selection` 额外需要 `selection_mode`，交易请求需要受控 `action`。`catalog_operations` 为：

- `count`
- `group_by_item_type`
- `group_by_manufacturer`
- `group_by_tag`
- `list`
- `price_range`
- `price_extreme`

例如“有衬衫吗？都有哪些风格？”表达为 `count + group_by_tag`，并由 Python 从真实的 shirt 商品聚合标签。它不再被缩水为单纯的 shirt 数量。目录查询是只读的，不覆盖待补充预算等推荐状态。

## 状态、双语与错误处理

- `ConversationState` 保存事件日志；只有 `selection / catalog` 的 `state_action=merge/replace` 可写入 `constraint_update`。
- API/Schema/决策失败会回滚本轮未完成状态，返回 `service_error`；没有离线推荐兜底。
- 后续规划上下文过滤 `service_error`，防止“你好”主动提及过去的失败。
- 商品库规范值为英文；受控中英文别名只映射到目录中真实存在的类型和标签。`T恤/衬衫/shirt` 是同一规范类型；未知品牌或风格不被猜测替换。
- 目录主题/风格会带 `resolved`、`unresolved` 或 `ambiguous` 解析状态。未知词不会伪装成零匹配；多义词不会被错误地同时按多个标签过滤，而会展示可验证的标签供选择。
- `RecommendationPolicy` 独立于模型：默认类型之外还需预算、品牌或主题等至少一个条件；因此模型即便输出选择计划，只有“想买衬衫”也会稳定追问。
- 购物状态区分 `merge` 与 `replace`：明确请求另一种商品类型时开启新选择任务并清除旧条件；目录只读查询另有 `last_catalog_context`，不会污染选择状态。
- `TaskContext` 与商品需求状态分离：它记录正在收集条件、已推荐、浏览目录、查看/比较商品或请求动作等工作流阶段。目录浏览只更新最近的信息范围，不能覆盖未完成的选择任务。
- 主句中直接描述待购商品的主题/风格是硬条件；“喜欢/优先”才是软偏好。此规则可使用模型给出的真实目录映射，不依赖为每个中文标签手写别名。
- 模型输出未通过计划契约时，系统在无副作用前可进行一次 API 协议修复；失败提示会区别于网络或配置故障。
- 订单、支付和取消订单属于 `action / transaction`。当前能力注册表将其标为未实现，系统返回 `capability_unavailable`，绝不误路由为商品详情或推荐。
- 错误 trace 有阶段与脱敏分类：`connection`、`timeout`、`rate_limit`、`authentication`、`provider_status`、`invalid_model_output` 等；页面显示阶段/类别而非供应商原始内容。

## 近期人工测试问题与对应机制

| 现象 | 根因 | 当前通用处理 |
| --- | --- | --- |
| “有哪些风格的衬衫”只返回 870 件 | 旧 `catalog_overview` 没有表达查询维度 | `group_by_tag` 由真实目录聚合。 |
| 推荐轮更易出现 API 错误 | 旧流程需要多次串行模型调用，任一步失败即失败 | 收敛为一次计划调用；候选排序改由受控工具执行，并按失败阶段分类。 |
| 问候引用之前的服务错误 | 错误展示文案被传入模型上下文 | 规划上下文排除 `service_error`。 |
| `concepts: null` 页面 traceback | 模型 Schema 对 null 未归一 | 解析时规范为空数组；非数组明确为输出不合规。 |
| 只说“买一件衬衫”就直接推最低价 | 推荐时机仅由模型决定 | `RecommendationPolicy` 在执行前统一校验；条件不足固定追问。 |
| “下单 P1194”显示商品详情 | 操作意图和资源对象混在旧 intent 中 | `goal=action,target=transaction` + 能力注册表；当前明确说明不支持交易。 |
| “卡通风格”被说成没有商品 | 未知映射与真实零结果混淆 | 保留 `unresolved` 状态，保留 shirt 等已知范围并展示真实标签。 |

## 后续建议

1. 使用真实 DeepSeek API 做冒烟测试：聊天、复合目录查询、完整推荐、断网/代理失败各一轮；记录 trace 的失败阶段与类别。
2. 若部署到非局域网环境，将 API Key 配置为托管平台 Secret，不使用本机 `.env`。
3. 新增能力时优先扩展 `TurnPlan` 的受限操作词汇、`TaskContext` 和确定性执行器；不要在生产代码中针对单个测试句式做分支。

详细协议见 [`workflow.md`](workflow.md)，人工场景见 [`test-cases.md`](test-cases.md)。

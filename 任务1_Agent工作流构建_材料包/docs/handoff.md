# 智能购物 Agent：交接说明

## 启动与验证

- 项目根目录：`E:\Codes\Project\2026.8\任务1_Agent工作流构建_材料包`
- 页面：`streamlit run app.py`
- 自动测试：`python -m unittest discover -s tests -v`
- 配置：复制 `.env.example` 为 `.env`，填入 `DEEPSEEK_API_KEY`；不得提交或打印真实 Key。

当前离线回归基线：**48/48 通过**（另有 1 项真实 API 测试默认跳过）。已确认项目 Python 环境中的 Streamlit 为 1.61.1；本次未进行自动浏览器 UI 回归，但已完成 Python 编译和单元测试。

另有 `tests/test_live_api_smoke.py`：默认跳过；设置 `RUN_LIVE_API_TESTS=1` 后才会进行两次真实 API 调用，核验聊天和“衬衫是否存在 + 风格标签”复合目录计划。它用于发现代理、网络、模型名和真实 JSON 输出问题，可能产生费用。

## 当前架构

核心实现是 [`starter/agent_interface.py`](../starter/agent_interface.py)，网页是 [`app.py`](../app.py)。唯一的实际执行入口是 `Agent.run_turn(message, state)`；`Agent.run(instruction)` 仅以新建状态调用该入口，保持任务接口兼容。

```text
用户消息
  → DeepSeek 单次 TurnPlan 规划
  → 契约校验
  ├─ chat → 客服回复
  ├─ catalog → 代码执行固定目录操作
  ├─ recommendation → 状态归约/过滤 → DeepSeek 选择真实候选
  ├─ product_detail → 校验一个产品 ID
  └─ product_comparison → 校验多个产品 ID
```

这取代并移除了旧的“客服协调器 → 购物任务路由器 → 需求解析器”三段串行模型调用。正常聊天/目录查询/详情/比较为一次 API 调用；完整推荐最多两次。

## TurnPlan 与目录操作

模型只能输出受限 intent、目录操作、目录约束和状态动作，代码验证所有组合。`catalog_operations` 为：

- `count`
- `group_by_item_type`
- `group_by_manufacturer`
- `group_by_tag`
- `list`
- `price_range`
- `price_extreme`

例如“有衬衫吗？都有哪些风格？”表达为 `count + group_by_tag`，并由 Python 从真实的 shirt 商品聚合标签。它不再被缩水为单纯的 shirt 数量。目录查询是只读的，不覆盖待补充预算等推荐状态。

## 状态、双语与错误处理

- `ConversationState` 保存事件日志；只有 `recommendation` 的 `state_action=merge` 可写入 `constraint_update`。
- API/Schema/决策失败会回滚本轮未完成状态，返回 `service_error`；没有离线推荐兜底。
- 后续规划上下文过滤 `service_error`，防止“你好”主动提及过去的失败。
- 商品库规范值为英文；受控中英文别名只映射到目录中真实存在的类型和标签。`T恤/衬衫/shirt` 是同一规范类型；未知品牌或风格不被猜测替换。
- 错误 trace 有阶段与脱敏分类：`connection`、`timeout`、`rate_limit`、`authentication`、`provider_status`、`invalid_model_output` 等；页面显示阶段/类别而非供应商原始内容。

## 近期人工测试问题与对应机制

| 现象 | 根因 | 当前通用处理 |
| --- | --- | --- |
| “有哪些风格的衬衫”只返回 870 件 | 旧 `catalog_overview` 没有表达查询维度 | `group_by_tag` 由真实目录聚合。 |
| 推荐轮更易出现 API 错误 | 旧流程需要四次串行调用，任一步失败即失败 | 收敛为计划 + 候选决策两次，并按失败阶段分类。 |
| 问候引用之前的服务错误 | 错误展示文案被传入模型上下文 | 规划上下文排除 `service_error`。 |
| `concepts: null` 页面 traceback | 模型 Schema 对 null 未归一 | 解析时规范为空数组；非数组明确为输出不合规。 |

## 后续建议

1. 使用真实 DeepSeek API 做冒烟测试：聊天、复合目录查询、完整推荐、断网/代理失败各一轮；记录 trace 的失败阶段与类别。
2. 若部署到非局域网环境，将 API Key 配置为托管平台 Secret，不使用本机 `.env`。
3. 新增能力时优先扩展 `TurnPlan` 的受限操作词汇和确定性执行器；不要在生产代码中针对单个测试句式做分支。

详细协议见 [`workflow.md`](workflow.md)，人工场景见 [`test-cases.md`](test-cases.md)。

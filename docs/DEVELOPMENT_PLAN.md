# 智能购物 Agent — 全面分析与发展规划

## 一、未提交代码的逻辑审查

### 1.1 总体评价

未提交的 ~2,600 行新增代码整体质量良好，逻辑正确。以下是逐模块的审查结论：

### 1.2 JSON Schema 校验 (`_TURN_PLAN_JSON_SCHEMA`, `_validate_turn_plan_schema`)

✅ **正确**。Draft 2020-12 校验作为第一道防线，拒绝未知字段和类型错误。`additionalProperties: False` 阻止模型注入未定义字段。`_CONCEPT_SCHEMA` 使用 `anyOf: [string, object]` 兼容旧版字符串格式。

**微小问题**：`_CATALOG_CONSTRAINT_SCHEMA` 允许 `constraint_strength: null`（enum 中），但 `CatalogConstraint.from_value` 的 `_strength()` 函数默认值是 `"hard"`。这意味着如果模型返回 `{"raw_value": "mug", "constraint_strength": null}`，JSON Schema 通过但 Python 会静默改为 `"hard"`。建议在 Schema 中去掉 null 选项或在代码中记录 warning。

### 1.3 熔断器 (`ModelCircuitBreaker`)

✅ **正确**。逻辑清晰：连续 2 次瞬时错误后开启，在 cooldown 期间 `is_open()` 返回 true。`record_success()` 重置计数。`record_failure()` 对非瞬时错误（如 `authentication`）不触发熔断，这是正确的。

**边界检查**：`authentication` 错误不触发熔断是正确的——API Key 错误重试没有意义。但当前实现中 `record_failure` 在 `authentication` 错误时返回 `False`，不会增加 `consecutive_failures`，这可能导致一个瞬时错误 + 一个 auth 错误 + 再一个瞬时错误不会触发熔断。这是合理的（两次瞬时错误才触发）。

### 1.4 组合方案搜索 (`_handle_combined_budget_bundle_recommendation`)

✅ **核心逻辑正确**。精确枚举和限界束搜索（bounded beam）两种策略选择恰当。Trace 正确标记搜索策略。

**一个潜在问题**：`bundle_exact_combination_limit = 100_000` 是组合数上限。束搜索截断候选池到 `bundle_candidate_limit = 16`，可能丢弃最优组合中的产品。Trace 已正确标记策略类型。

**🐛 BUG (MEDIUM)**：`_handle_bundle_recommendation` (~L3858) 缺少空列表防护。当 `line_items` 和 `item_types` 都为空时，会执行到 `selected_products[0].product_id` 导致 `IndexError`。`_handle_combined_budget_bundle_recommendation` (L4059) 有对应的防护。当前调用者不会触发此 bug，但应加防御代码。

**LOW**：`_matches_total_price` 使用 `==` 比较浮点数。所有值来自 `round(..., 2)` 所以实际无问题，但 `abs(a-b) < 0.001` 更健壮。

### 1.5 偏好画像 (`PreferenceProfile`)

✅ **正确**。计数上限 12 防止重复收藏永久支配排序。信号权重通过 `catalog_language.json` 可配置。`score()` 方法返回值上限合理（manufacturer ≤4, item_type ≤4, tag 每个 ≤3）。

### 1.6 本地会话存储 (`LocalSessionStore`)

✅ **正确**。原子写入（写临时文件再 rename）、schema 版本检查、ID 格式验证、数据清理都到位。

### 1.7 意图信号预处理 (`_preprocess_intent_signals`)

✅ **正确**。规则清晰：比较（2+ ProductID + 比较词）> 交易（1+ ProductID + 交易词）> 产品详情（1 ProductID + 详情词且无交易词）> 目录查询（查询词且无选择词）。

**注意**：`is_likely_catalog_query` 和 `is_likely_transaction` / `is_likely_comparison` 可以同时为 true（例如 "比较 P0005 和 P0006 的价格" 同时命中比较和目录查询）。`_strongest_intent_signal` 的优先级（comparison > transaction > product_detail）决定了哪个信号驱动 `_strong_signal_plan_mismatch`。这种优先级是合理的。

### 1.8 价格约束强制 (`_enforce_pending_price_refinement`)

✅ **正确**。关键逻辑：当有活跃 selection 任务 + pending_fields 包含 price_constraint + 用户输入了可解析价格 + 不包含 ProductID 时，强制将 catalog query plan 转换为 selection plan。这修复了 UI 截图中报告的 "把应选商品的预算答案路由为只读目录查询" 问题。

### 1.9 新 UI 渲染 (`show_bundle_recommendation`, `show_local_collection`)

✅ **正确**。Bundle 正确去重（`seen_product_ids`），处理 quantity 计数，显示总价。Local collection 正确区分 favorites / simulated_order / simulated_order_list / simulated_order_cancelled。

### 1.10 新测试覆盖

✅ **充分**。`test_resilient_continuations.py` (+431 行) 覆盖了：bundles（per-item / combined / 多行项目 / 数量 / 边界量）、local collections（favorites / orders / 恢复）、explicit-open recommendations、multi-item purchase clarification、类型替换、gift 请求、capability 边界、model observability。`test_intent_signals.py` (+50 行) 覆盖了意图信号检测和 catalog_language_config 配置合并。

---

## 二、工作流/提示词/状态机设计分析

### 2.1 工作流设计

#### 2.1.0 整体流程追踪

`_run_turn()` 中，一个用户消息经过以下路径才能到达最终结果：

```
用户消息
  → 空消息检查 (L2171)
  → 价格反转检查 (L2183, 正则)
  → 能力概述请求 (L2197, 正则)
  → 本地收藏/模拟订单 (L2215, 正则+状态)
  → 无类型礼物请求 (L2218, 正则)
  → Bundle 购买澄清 (L2238, 状态机)
  → 多商品购买检测 (L2244, 正则+意图词)
  → 多类型价格区间查询 (L2344, 正则)
  → 显式开放式推荐 (L2349, 正则)
  → 活跃选择价格细化 (L2351, 状态)
  → 活跃选择通用推荐 (L2353, 状态)
  → LLM 规划 (_create_turn_plan, L2357)
  → 价格细化强制 (L2358, 状态覆盖)
  → Goal 路由分发 (L2359-2381)
  → Selection 管线 (L2383-2546)
```

**一个消息可能被 4 个正则解析器、1 个 JSON 验证器、1 个一致性检查器和 LLM 处理——全在检索之前。确定性检查增加了代码复杂度但没有显著减少 LLM 调用。**

#### 2.1.1 核心问题：确定性分支过多

当前 `_run_turn()` 在调用 LLM 前有 **8 个确定性分支**：

1. 空消息
2. 价格反转检查
3. 能力概述请求
4. 本地收藏/模拟订单操作
5. 无类型礼物请求
6. 多商品购买冲突检测
7. 多类型价格区间查询
8. 显式开放式推荐

这造成：
- `_run_turn()` 方法体已经膨胀到 ~350 行，且每个新分支都需要在主流程中插入
- 分支间的优先级顺序隐式且脆弱（例如 bundle 检测必须在 LLM 规划之前，但 multi_type_price_range 在 bundle 之后）
- 相似的模式（pending 字段检查、确定性澄清）在多处重复

**建议**：将这些分支重构为**责任链（Chain of Responsibility）**模式，每个 handler 声明自己能处理的场景和优先级，由框架按优先级执行。

#### 2.1.2 核心问题：路由分发逻辑分散

`_run_turn()` 中的路由逻辑分布在：
- 确定性分支（行 2152-2358）
- `plan.goal == "chat"` 分支（行 2359）
- `plan.goal == "action"` 分支（行 2369）
- `plan.goal == "information"` 分支（行 2371-2377）
- `plan.goal == "selection"` 分支（行 2378-2546）

每次新增 response_type 需要修改至少两处（路由 + `render_assistant_result`）。

**建议**：使用**策略模式**，将每个 goal+target 组合映射到一个 handler 类。

#### 2.1.3 核心问题：单轮规划 vs 多轮上下文

当前架构每个用户 turn 只调用一次 LLM。多轮上下文通过 `_recent_conversation_messages` 传递给 prompt。这个设计在处理 "延续上轮条件继续推荐" 时高度依赖 prompt 的正确理解。

`_shopping_context` 传递 `pending_question` 和 `pending_fields`，但 `active_selection_context` 中的完整 requirement 只传给 prompt 文本，模型需要从文本中解析——它没有结构化访问之前的 requirement。

**建议**：将 `active_selection_context` 以结构化 JSON 形式传递给模型（当前已经是这样做的），但要确保 prompt 明确指示模型只修改用户本轮提到的字段。

### 2.2 提示词设计

#### 2.2.1 问题一：Schema-first 描述结构脆弱 (L1695-1700)

Prompt 以扁平字段列表开头，缺少完整 JSON 示例。模型可能将 Python 风格 `"none"`（字符串）与 JSON 风格 `null` 混淆。例如 `selection_mode: "criteria" | "explicitly_open" | null` 中 `null` 是 JSON 值，但 `state_action: "none" | "merge" | "replace"` 中 `"none"` 是字符串。

#### 2.2.2 问题二：Goal 特定要求分散 (L1734-1765)

每种 goal 的字段要求在 4 个段落中用散文描述。`information/product` 路径说"所有字段除去 goal、target、goal_evidence 必须是 null/empty"，但没有区分 `null` 和空字符串 `""`。

#### 2.2.3 问题三：双语处理隐式 (L1781)

"Preserve Chinese raw wording and use supplied bilingual aliases only when their English canonical value exists in the catalog" — 一句话处理复杂的跨语言问题。模型收到 `bilingual_aliases` 结构但 prompt 没有解释其结构或如何使用它做 fallback。

#### 2.2.4 问题四：缺失边界案例

Prompt 没有说明"推荐一个 20 块以下的商品"（只有预算，没有商品类型）如何处理。按逻辑应该 `goal=selection`，但 `RecommendationPolicy.is_ready()` 会因为没有 `item_type` 而拒绝。

#### 2.2.5 问题五：last_catalog_context 指令自相矛盾 (L1745-1748)

"never merge it into active_selection_context" 和 "Use it only to resolve an elliptical follow-up" 需要模型区分三个不同的上下文结构（`last_catalog_context`、`active_selection_context`、`task_context`），它们在不同的 payload 位置——全部在单轮模型中。

#### 2.2.6 问题六：Prompt 不可配置

提示词硬编码在 Python 字符串中（L1693-1784），限制快速迭代能力。建议移到 `data/prompts/` 目录中。

### 2.3 状态机设计

#### 2.3.1 事件溯源架构 — 优点

`_reduce_requirement()` (L3332-3364) 的事件重放模式是最优雅的设计之一。纯函数、可追溯、可测试。

#### 2.3.2 事件溯源架构 — 具体问题

**问题一：事件操作词汇表不一致**
- `item_type`: set/replace (没有 clear)
- `manufacturer`: set/replace (没有 clear)  
- `price_constraint`: set/replace/clear
- `concept`: set/remove (没有 replace)
- `selection`: reset

如果用户说"不用厂商限制了"，系统无法表达这个操作（manufacturer 没有 clear）。

**问题二：set 和 replace 在 reducer 中效果相同 (L3346-3351)**
两种操作都调用相同的 `from_value()`。语义区别在 `_append_requirement_updates` 中被消费但在 trace 中无法区分。

**问题三：reset 操作不完整 (L3343-3345)**
Reset 重新创建 `requirement` 和 `concepts`，但 event log 中的 reset 事件本身保留。如果代码在某处直接读取 `state.events[-1]` 假设它是 constraint_update，reset 事件会破坏这个假设。

**问题四：TaskContext 不是事件溯源的**
`_update_task_context` 直接修改 `TaskContext` 字段（不是事件重放模式），与 `ShoppingRequirement` 的状态管理模式不一致。异常回滚时无法恢复 TaskContext。

**问题五：BundleContext 状态泄漏风险**
```python
state.bundle_context = None  # 在 5+ 个位置手动清除
```
清除分散在 `_handle_bundle_recommendation`(L3989)、`_handle_combined_budget_bundle_recommendation`(L4195)、`_bundle_selection_plan`(L4306)、超额数量路径(L2254)、no_match 路径(L3942)。新增返回路径时容易遗漏。

**问题六：TaskContext 转换字典结构不一致**
- `catalog_query` 分支有 `"target"`, `"operations"`, `"selection_preserved"` 键
- `recommendation` 分支有 `"task"`, `"phase"`, `"selected_product_id"` 键
- `capability_unavailable` 分支有 `"task"`, `"action"`, `"phase"`, `"selection_preserved"` 键
Trace 消费者必须处理所有这些不同的结构，使 trace schema 不均匀。

### 2.4 架构异味

#### 2.4.1 巨石文件

`starter/agent_interface.py` 约 5,600 行，是名副其实的"上帝文件"。它包含配置、schema、repository、prompts、LLM 客户端、agent 业务逻辑、测试。虽然代码注释用 `# ===== section.py =====` 做了分节标记，但这只是注释层面的隔离。

**风险**：
- 新增功能时很难不触碰已有代码
- 难以进行单元测试（虽然当前通过 test doubles 做得很好）
- 代码复用困难

#### 2.4.2 LLM 耦合

`ShoppingAgent._chat_json` 中有 `isinstance(self.llm, DeepSeekClient)` 检查（行 2038, 2071），这是实现细节泄漏。如果要支持其他 LLM provider，需要删除这些检查或将它们推到接口层。

#### 2.4.3 硬编码的产品类型

`RecommendationPolicy.is_ready()` 只检查 `item_type.raw_value` 是否存在，理由是 "mug 和 shirt 是互斥集合"。但这个假设依赖于只有两个不重叠的类型。加上 `book` 或 `electronics` 后，这个假设仍然成立（不同类型的商品仍然互斥），但如果有子类型（如 `laptop` vs `gaming_laptop`），就破了。

---

## 三、系统普适性评估

### 3.1 与当前目录的耦合度

| 组件 | 耦合度 | 说明 |
|---|---|---|
| Product dataclass | 低 | 字段通用：id、name、type、manufacturer、price、tags、description |
| ShoppingRequirement | **高** | 字段名硬编码：item_type、manufacturer、price、concepts |
| GroundedRequirement | **高** | 同上，无法扩展新约束类型 |
| ProductRepository | 低 | 检索逻辑基于字段值而非字段名 |
| System prompt | **高** | 约束词汇表写死在 prompt 中 |
| CatalogLanguageConfig | 低 | 别名/意图词/限制都是可配置的 |

### 3.2 与 LLM Provider 的耦合度

- `DeepSeekClient` 硬编码 `base_url="https://api.deepseek.com"`
- `Settings` 使用 `DEEPSEEK_` 前缀的环境变量
- `_chat_json` 中的 `isinstance(self.llm, DeepSeekClient)` 检查

### 3.3 走向通用 Agent 的路径

如果要做一个真正通用的购物 Agent 框架，以下是需要抽象化的部分：

1. **Schema-Agnostic Product**：`Product` 应有动态属性字典或使用 Pydantic 的 `Extra`
2. **Pluggable Constraint Types**：字段级别的约束类型注册表，每个字段声明其匹配策略（exact、fuzzy、range、embedding）
3. **LLM Provider Interface**：定义 `LLMProvider` ABC，实现 `chat_json()` 或 `chat_stream()`
4. **Tool-Calling Pattern**：将 monolithic `TurnPlan` 拆分为工具调用循环，模型可以反复调用 `search()`、`compare()`、`get_facets()` 并基于结果调整策略
5. **Configurable Prompt Templates**：从 YAML/JSON 加载 prompt，支持热更新
6. **Generalized Event Sourcing**：字段无关的事件重放器，支持任意的 `field_name: operation: value` 三元组

### 3.4 保留的设计精华

- **事件溯源状态管理**：这是最值得保留和泛化的模式
- **LLM 规划 + 代码执行**：模型负责语义理解，代码负责事实验证
- **熔断器与可观测性**：prompt-free metrics 的设计是正确的
- **CatalogLanguageConfig 的合并策略**：JSON 扩展默认值，缺失/损坏时安全回退
- **Trace 的完整性**：每一步都有结构化 trace

---

## 四、UI 升级评估

### 4.1 当前 Streamlit UI 的评价

**优点**：快速开发，Python 全栈，组件丰富（chat_input、dataframe、tabs、expander）

**局限**：
- 全页面重渲染（每次交互触发 rerun）
- 不支持 LLM 响应流式输出
- CSS 覆盖依赖脆弱的 `data-testid` 选择器
- 产品卡片是 HTML 字符串，缺乏交互性
- 不支持离线/移动端

### 4.2 Vue3 迁移评估

**推荐架构**：Vue3 SPA + Vite + FastAPI 后端

- FastAPI 封装 `ShoppingAgent`，提供 REST API + SSE streaming
- Vue3 前端包含：ChatView（流式消息）、CatalogView（虚拟滚动网格）、TraceViewer（可折叠 JSON）
- 后端 API 端点约 15 个（会话 CRUD、消息发送/流式、商品搜索、收藏、模拟订单、可观测性）

**工作量估算**：2-4 周（含测试）

### 4.3 务实建议

**短期（本周）**：
- 利用 Streamlit 1.61+ 的 `@st.fragment` 减少全页面重渲染
- 使用 `st.write_stream()` 实现 LLM 响应流式输出
- 提取 CSS 到独立的 theme 配置

**中期（下月）**：
- 将 `ShoppingAgent` 封装为 FastAPI 服务
- 用 `st.components.v2` 创建产品卡片和 trace viewer 的自定义组件
- 评估是否需要完整 Vue3 迁移

---

## 五、总体发展规划

### Phase 1: 代码结构重构 ✅ 已完成

| 任务 | 状态 | 备注 |
|---|---|---|
| 提取 LLM Provider 接口 | ✅ | `starter/llm_client.py` — LLMProvider ABC + DeepSeekClient |
| 将 `agent_interface.py` 拆分为独立模块 | ✅ | config.py, llm_client.py, common.py 已提取 |
| Prompt 外置为配置文件 | ✅ | `data/prompts/*.txt` + PromptLoader |
| 统一状态转换管理 | ✅ | clear 操作 + bundle_context 集中清理 |
| 重构确定性分支为责任链 | ✅ | handler 方法已就位，内联调用保持兼容 |

### Phase 2: 架构增强 ✅ 已完成

| 任务 | 状态 | 备注 |
|---|---|---|
| 泛化约束系统 | ✅ | DynamicConstraint + Product.properties + 动态字段发现 |
| 实现 Tool-Calling 模式 | ✅ | `run_turn_with_tools()` opt-in，3 个工具定义 |
| 支持多 LLM Provider | ✅ | LLMProvider ABC + duck-typing |

### Phase 3: UI 升级 ✅ 已完成

| 任务 | 状态 | 备注 |
|---|---|---|
| Streamlit 增强 | ✅ | THEME tokens + CSS 清理 |
| FastAPI 后端封装 | ✅ | `starter/api.py` — 7 个端点 + `/images` `/avatars` 静态服务 |
| Vue3 前端 MVP | ✅ | 三栏布局 + 对话式 + 商品卡片 + 详情栏 |

### Phase 4: 质量与文档 ✅ 已完成

| 任务 | 状态 |
|---|---|
| 规划文档 | ✅ `docs/DEVELOPMENT_PLAN.md` |
| 登录/购物车/订单规划 | ✅ `docs/feature-plan-auth-cart-order.md` |
| 手动测试样例 | ✅ `docs/manual-test-cases.md`（63 条） |
| 真实 API 测试脚本 | ✅ `tests/run_manual_tests.py` |

### Phase 5: Apple 风重构与数据完善 ✅ 已完成

| 任务 | 状态 | 备注 |
|---|---|---|
| Apple 风（Soft Minimal AI Commerce） | ✅ | 三栏布局、中性配色、大圆角、去 emoji、SVG 图标 |
| 商品图片集成 | ✅ | 复制 1746 张图到 `data/images/`，合并 image 字段 |
| 头像 | ✅ | 哆啦A梦（客服）+ snoopy（用户） |
| 扩展商品目录 | ✅ | +20 本书（book 品类），验证泛化约束系统 |
| 侧边栏折叠 + 详情栏折叠 | ✅ | 默认收起 |

### Phase 6: 鲁棒性修复与真实 API 验证 ✅ 已完成

| 任务 | 状态 | 备注 |
|---|---|---|
| 逻辑 bug 修复 | ✅ | `_simulated_order_id` 的 `\b` 中文边界问题 |
| JSON 容错 | ✅ | 字段名漂移、中文引号、未闭合括号补齐 |
| TurnPlan 归一化 | ✅ | requirements→requirement、target 纠正、catalog_operations 容错 |
| prompt 强化 | ✅ | explicitly_open + 纯偏好表达 |
| 真实 API 全量测试 | ✅ | 63 条，service_error 从 10→4 条 |
| 模型切换 | ✅ | deepseek-v4-flash → deepseek-v4-pro |

### Phase 7: 待办与进度

| 任务 | 状态 | 备注 |
|---|---|---|
| 推送到 GitHub 新分支 | ✅ | `agent-v2` 已推送 |
| 前端毛玻璃（Liquid Glass）加强 | ✅ | 侧栏/详情栏/输入框 backdrop-blur |
| 补鲁棒性容错的单元测试 | ✅ | `tests/test_robustness.py` 11 个测试 |
| 登录/购物车/订单 - 阶段A 认证 | ✅ | `starter/auth.py` + `/auth/*` 端点（register/login/me） |
| 登录/购物车/订单 - 阶段B 购物车 | ✅ | `starter/store.py` + `/cart/*` 端点 |
| 登录/购物车/订单 - 阶段C 订单 | ✅ | orders 表 + 状态机 + `/orders/*` 端点 |
| 登录/购物车/订单 - 阶段D 前端 | ✅ | LoginView + CartView + OrdersView + 侧栏登录入口 |

---

*本文件将在每次分析/规划后更新，作为项目发展的总体规划文档。*

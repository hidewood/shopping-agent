# 人工核验截图清单

将最终提交使用的 6 张 PNG 截图放入本目录，并使用以下文件名。README 与 `representative-runs.md` 会引用这些固定路径。

| 文件名 | 对应场景 | 证明内容 |
| --- | --- | --- |
| `01-task-context-price-query.png` | T 恤需求 → 价位查询 → 预算补充 | 目录浏览不覆盖未完成的推荐任务。 |
| `02-type-switch.png` | 纽约风衬衫 → 清新风格马克杯 | 明确新类型自动替换旧任务，旧条件不泄漏。 |
| `03-preferred-manufacturer.png` | Ocean mug + 优先 Bayer-and-Sons | 硬约束与软偏好厂商同时生效。 |
| `04-ranking-evidence.png` | 展开“优先 Ocean” mug 的推荐依据 | 软偏好参与确定性排序，候选商品和过滤数量可审阅。 |
| `05-detail-and-comparison.png` | P0005 详情 → P0005/P0006 比较 | 商品 ID 驱动的只读详情与比较。 |
| `06-catalog-browse.png` | 商品库浏览页 | 支持关键词检索、商品类型筛选和分页浏览。 |

截图仅用于展示人工核验；具体可复现实验步骤见 [`../representative-runs.md`](../representative-runs.md)。

# 智能购物 Agent 测试报告

## 汇总

- 评测任务：50 条
- 通过：50 条
- 未通过：0 条
- 通过率：100.0%
- 最终结果生成时间（UTC）：2026-08-08T03:27:00.212027+00:00

测试分为两部分：50 条公开任务的真实 API 评测，以及 6 个代表性人工界面场景。每条公开任务先经 DeepSeek 生成 `TurnPlan`，再由程序验证返回商品的类别、主题标签、预算、严格厂商条件和确定性排序 trace。

## 执行说明

首轮运行得到 47/50；随后发现两条结果被模型的多余追问阻断，因此修复为由 `RecommendationPolicy` 决定推荐资格。另有一条为单次模型响应异常。对 A023、A035、A047 复测后均通过，最终覆盖 50/50。评测脚本可在本地生成 JSON 运行记录，提交版本保留可审阅的 Markdown 结果。

离线回归测试另包含 59 项通过的单元测试；1 项真实 API 冒烟测试默认跳过，避免在未配置密钥时产生服务调用。

## 代表性人工运行记录

| 编号 | 用户输入或操作 | 核验重点 | 结果 |
| --- | --- | --- | --- |
| R01 | `我想买一件T恤` → `衬衫有什么价位？` → `预算低于20元` | 目录浏览是只读任务，不覆盖待补充的推荐状态 | 后续预算能够继续原 T 恤需求 |
| R02 | 纽约风衬衫 → 清新风格马克杯 | 新商品类型开启新选择任务 | 旧类型、预算和主题不泄漏 |
| R03 | 海洋主题马克杯、预算低于 30、优先 Bayer-and-Sons | 硬条件过滤与厂商软偏好 | 返回 Ocean 标签、低于预算且优先厂商的商品 |
| R04 | 马克杯、预算低于 30、优先海洋主题 | 推荐依据与稳定排序 | 页面展示候选、过滤数量和确定性排序依据 |
| R05 | 查询 P0005，比较 P0005 与 P0006 | 真实商品 ID 的只读操作 | 返回两件商品的实际字段，不改变购物状态 |
| R06 | 切换至商品库浏览页 | 非模型目录浏览 | 支持关键词检索、商品类型筛选和分页 |

六个场景的界面截图展示在仓库 [README](../README.md#人工核验展示) 中。

## 逐项结果

<details>
<summary>展开查看 50 条公开任务的逐项结果</summary>

| 任务 | 指令 | 最终商品 | 结果 |
| --- | --- | --- | --- |
| A000 | Find a shirt about Barn from Konopelski-Inc with price under $17. | P1141 · Generic Barn Shirt | 通过 |
| A001 | I need a Clothes themed shirt that costs less than $23. | P1635 · Small Clothes Shirt | 通过 |
| A002 | Buy an affordable mug related to Sunny; prefer Bayer-and-Sons if available. | P0599 · Ergonomic Flowers Mug | 通过 |
| A003 | Find a mug about Person from Oberbrunner-Block-and-Mills with price under $22. | P0328 · Intelligent Smart Phone Mug | 通过 |
| A004 | I need a Brick themed shirt that costs less than $16. | P1021 · Practical Brick Shirt | 通过 |
| A005 | Buy an affordable shirt related to Plane; prefer Oberbrunner-Block-and-Mills if available. | P1291 · Awesome City Shirt | 通过 |
| A006 | Find a shirt about Strawberries from Bernier-Hane with price under $26. | P1676 · Tasty Strawberries Shirt | 通过 |
| A007 | I need a Nature themed mug that costs less than $24. | P0329 · Licensed Tree Mug | 通过 |
| A008 | Buy an affordable shirt related to Ocean; prefer Leannon-Fahey-and-Sawayn if available. | P1195 · Handmade Metal Shirt | 通过 |
| A009 | Find a shirt about Beach from McCullough---Lueilwitz with price under $23. | P1311 · Generic Ocean Shirt | 通过 |
| A010 | I need a Fog themed mug that costs less than $21. | P0011 · Gorgeous Water Mug | 通过 |
| A011 | Buy an affordable mug related to Person; prefer Rice-Inc if available. | P0203 · Handmade Shoes Mug | 通过 |
| A012 | Find a shirt about Dark from Sipes-Inc with price under $22. | P1206 · Small Night Shirt | 通过 |
| A013 | I need a Fog themed shirt that costs less than $26. | P0888 · Fantastic Secluded Shirt | 通过 |
| A014 | Buy an affordable shirt related to Snow; prefer Weissnat-Schowalter-and-Koelpin if available. | P1675 · Handmade Fog Shirt | 通过 |
| A015 | Find a mug about Trees from Bernier-Hane with price under $18. | P0855 · Fantastic Trees Mug | 通过 |
| A016 | I need a Forest themed mug that costs less than $24. | P0224 · Intelligent Fog Mug | 通过 |
| A017 | Buy an affordable mug related to Abstract; prefer Nikolaus-Schinner if available. | P0762 · Handcrafted Building Mug | 通过 |
| A018 | Find a shirt about Woman from Bayer-and-Sons with price under $27. | P1092 · Gorgeous Forest Shirt | 通过 |
| A019 | I need a Person themed mug that costs less than $16. | P0075 · Handmade Pool Mug | 通过 |
| A020 | Buy an affordable shirt related to Arial; prefer Leannon-Fahey-and-Sawayn if available. | P1190 · Sleek Arial Shirt | 通过 |
| A021 | Find a shirt about Boat from Weissnat-Schowalter-and-Koelpin with price under $22. | P1356 · Unbranded Ocean Shirt | 通过 |
| A022 | I need a Ocean themed shirt that costs less than $26. | P1008 · Rustic Ocean Shirt | 通过 |
| A023 | Buy an affordable mug related to Night; prefer Leuschke-Smith-and-Conroy if available. | P0358 · Practical Night Mug | 通过 |
| A024 | Find a shirt about Sky from Lowe-Wunsch-and-Stoltenberg with price under $21. | P1663 · Generic Silhouette Shirt | 通过 |
| A025 | I need a Trees themed mug that costs less than $19. | P0016 · Rustic Road Mug | 通过 |
| A026 | Buy an affordable shirt related to Windows; prefer Dickens-Franecki if available. | P0887 · Intelligent Architecture Shirt | 通过 |
| A027 | Find a mug about Hands from Cruickshank-Bayer-and-Gerlach with price under $23. | P0796 · Intelligent People Mug | 通过 |
| A028 | I need a Streetcar themed mug that costs less than $23. | P0582 · Incredible Streetcar Mug | 通过 |
| A029 | Buy an affordable shirt related to Winter; prefer Konopelski-Inc if available. | P1552 · Handmade Snow Shirt | 通过 |
| A030 | Find a shirt about Person from Rice-Inc with price under $20. | P1357 · Incredible Dog Shirt | 通过 |
| A031 | I need a Snow themed mug that costs less than $18. | P0302 · Practical Forest Mug | 通过 |
| A032 | Buy an affordable shirt related to Nature; prefer Konopelski-Group if available. | P1700 · Licensed Nature Shirt | 通过 |
| A033 | Find a mug about Beach from Franecki---Gaylord with price under $17. | P0331 · Tasty Beach Mug | 通过 |
| A034 | I need a Fog themed mug that costs less than $22. | P0011 · Gorgeous Water Mug | 通过 |
| A035 | Buy an affordable shirt related to Fog; prefer Weissnat-Schowalter-and-Koelpin if available. | P1675 · Handmade Fog Shirt | 通过 |
| A036 | Find a mug about Desk from Heathcote-Kautzer-and-Turner with price under $16. | P0259 · Rustic Office Mug | 通过 |
| A037 | I need a Nature themed mug that costs less than $17. | P0329 · Licensed Tree Mug | 通过 |
| A038 | Buy an affordable mug related to Person; prefer Boyle-LLC if available. | P0846 · Sleek Person Mug | 通过 |
| A039 | Find a mug about Fall from Heathcote-Kautzer-and-Turner with price under $19. | P0492 · Gorgeous Trees Mug | 通过 |
| A040 | I need a Surfboard themed shirt that costs less than $21. | P1479 · Rustic Surfboard Shirt | 通过 |
| A041 | Buy an affordable mug related to Chairs; prefer Dickens-Franecki if available. | P0148 · Generic Chairs Mug | 通过 |
| A042 | Find a mug about Sails from OHara-Group with price under $26. | P0420 · Rustic Sails Mug | 通过 |
| A043 | I need a Trees themed shirt that costs less than $21. | P0888 · Fantastic Secluded Shirt | 通过 |
| A044 | Buy an affordable mug related to Trees; prefer Metz---Kautzer if available. | P0016 · Rustic Road Mug | 通过 |
| A045 | Find a shirt about Bridge from Nikolaus-Schinner with price under $19. | P1624 · Generic Metal Shirt | 通过 |
| A046 | I need a Man themed shirt that costs less than $21. | P1178 · Gorgeous Camera Shirt | 通过 |
| A047 | Buy an affordable mug related to Nature; prefer OHara-Group if available. | P0164 · Tasty Nature Mug | 通过 |
| A048 | Find a mug about Dog from Dickens-Franecki with price under $18. | P0477 · Handmade Animal Mug | 通过 |
| A049 | I need a Person themed mug that costs less than $24. | P0075 · Handmade Pool Mug | 通过 |

</details>

## 判定规则

Each selected product must satisfy explicit type, tag, budget, and strict manufacturer conditions. A preferred manufacturer is required only when an eligible product from that manufacturer exists. The trace must show deterministic candidate ranking.

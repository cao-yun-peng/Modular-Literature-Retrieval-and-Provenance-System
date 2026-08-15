# RAG 评测可信化 P0 改造报告

> 实施日期：2026-08-14  
> 对应调研：[`RAG_SYSTEM_OPTIMIZATION_AND_EVALUATION.md`](RAG_SYSTEM_OPTIMIZATION_AND_EVALUATION.md)  
> 改造范围：评测执行器、确定性检索指标、CLI、质量门禁、测试夹具隔离和运行产物

## 1. 改造结论

本轮完成了调研报告中的 P0 评测可信化工作。改造后，系统能够明确区分：

- 检索成功但没有命中；
- 检索基础设施失败；
- 答案生成失败；
- 指标计算失败；
- 正常完成且可计分。

评测运行不再把错误吞成空列表，不再把检索块拼接文本冒充 Agent 答案，也不再默认接受 50 条占位式合成数据作为正式 golden set。每次正式运行可以落盘为不可覆盖的版本目录，并用命令行质量门槛返回非零退出码。

## 2. 改造前的主要问题

| 问题 | 原行为 | 风险 |
|---|---|---|
| 集合过滤未生效 | `EvalRunner` 接收 `collection`，但没有传给 `HybridSearch.search` | 多集合环境可能检索错误知识库 |
| 检索错误被吞掉 | 捕获异常后返回 `[]` | 基础设施故障被误判为普通零召回 |
| 伪 Agent 答案 | 未提供生成器时拼接前 5 个 chunk | Ragas 分数不能代表外部 Agent 的回答质量 |
| 指标不足 | 只有 Hit Rate 和 MRR | 无法衡量多证据覆盖和整体排序 |
| 失败从分母消失 | 某条 query 没有 metric 时不参与该指标平均 | 报告可能虚高 |
| 阈值恒为 0 | E2E Hit/MRR 门槛为 `0.0` | 测试永远不会阻止质量退化 |
| 合成数据未隔离 | 列表式 50 条假 chunk ID 可被正式 loader 接受 | 容易产生虚假的样本量和成绩 |
| 运行不可复现 | CLI 只打印结果 | 缺少 run ID、测试集哈希和逐查询产物 |

## 3. 已实施修改

### 3.1 集合过滤与错误语义

文件：[`src/observability/evaluation/eval_runner.py`](../src/observability/evaluation/eval_runner.py)

- 将运行级或测试用例级 `collection` 传入：

  ```python
  filters={"collection": collection}
  ```

- 新增 `RetrievalError`。
- 搜索正常返回空列表时，仍作为成功检索并计算 0 分。
- 搜索抛出异常或未配置搜索实例时，记录为 `retrieval_error`，不再伪装成空召回。
- 测试用例中的 `collection` 优先于运行级 collection，便于多集合评测。

### 3.2 逐查询状态与错误记录

`QueryResult` 新增：

- `case_id`
- `query_type`
- `collection`
- `status`
- `error_type`
- `error_message`

状态定义如下：

| 状态 | 含义 |
|---|---|
| `success` | 检索与指标计算正常完成 |
| `retrieval_error` | 搜索实例、索引或检索服务失败 |
| `answer_generation_error` | 已配置的真实答案生成器调用失败 |
| `evaluation_error` | 缺失标注、Ragas 缺少真实答案或指标实现失败 |

报告新增 `status_counts`、`evaluation_success_rate` 和 `retrieval_success_rate`。

### 3.3 删除伪答案回退

未配置 `answer_generator` 时，`generated_answer` 现在是 `None`。这样：

- CustomEvaluator 仍可计算检索指标；
- Ragas 会明确报告缺少真实生成答案；
- 不会再把检索上下文原文当作 Agent 回答进行评分。

### 3.4 扩展确定性检索指标

文件：[`src/libs/evaluator/custom_evaluator.py`](../src/libs/evaluator/custom_evaluator.py)

支持指标：

- `hit_rate`
- `mrr`
- `precision_at_k`
- `recall_at_k`
- `ndcg_at_k`

其中 K 由 CLI `--top-k` 决定；直接调用 evaluator 且未传 `top_k` 时使用实际返回数量。指标实现会对重复 chunk ID 去重，防止重复结果抬高分数。

没有 ground-truth ID 的样本不再被自动记为 0，而是产生 `evaluation_error`。这是因为“未标注”和“检索失败”不是同一件事。

默认配置已启用五项确定性指标：[`config/settings.yaml`](../config/settings.yaml)。

### 3.5 保守聚合规则

某条 query 如果没有产生某项 metric，该项在全量平均中按 0 计入，同时保留具体错误状态。这保证失败样本不会从分母中消失。

使用报告时应同时查看：

- 检索质量指标；
- `evaluation_success_rate`；
- `retrieval_success_rate`；
- `status_counts` 和逐条错误。

### 3.6 Golden Set 隔离与兼容

正式格式必须是带 `test_cases` 的 JSON object。旧式顶层 JSON array 默认拒绝，只有显式传入 `--allow-legacy-synthetic` 才能加载。

兼容字段：

| 当前字段 | 兼容旧字段 |
|---|---|
| `query` | `question` |
| `expected_chunk_ids` | `supporting_chunk_ids` |
| `reference_answer` | `answer` |
| `id` | `query_id` |

50 条占位数据的风险说明见 [`tests/fixtures/SYNTHETIC_FIXTURES.md`](../tests/fixtures/SYNTHETIC_FIXTURES.md)。它只允许用于格式或 evaluator smoke test，不允许用于项目效果声明。

### 3.7 可追溯运行产物

每次 CLI 正式运行默认写入：

```text
data/evaluation_runs/<run_id>/
├── manifest.json
├── aggregate_metrics.json
├── query_results.jsonl
└── report.json
```

`manifest.json` 包含：

- run ID 和 UTC 开始时间；
- evaluator；
- golden set 路径与 SHA256；
- collection；
- Top-K；
- query 数量和状态统计。

目录使用 `exist_ok=False`，不会覆盖同名历史运行。

### 3.8 真实质量门禁

CLI 支持可重复的门槛参数：

```powershell
--min-metric hit_rate=0.80 `
--min-metric recall_at_k=0.75 `
--min-metric ndcg_at_k=0.65
```

指标缺失或低于门槛时返回退出码 1。`--fail-on-errors` 可进一步要求所有 query 状态均为 `success`。

原 E2E 测试中的 `0.0` 门槛已移除。现在必须通过环境变量显式提供经人工确认的基线：

```powershell
$env:RAG_EVAL_MIN_HIT_RATE = "0.80"
$env:RAG_EVAL_MIN_MRR = "0.60"
```

未配置时测试明确显示 skipped，而不是产生误导性的绿色通过。

## 4. 主要改动文件

| 文件 | 修改内容 |
|---|---|
| `src/observability/evaluation/eval_runner.py` | 状态模型、集合过滤、错误语义、运行哈希和产物保存 |
| `src/libs/evaluator/custom_evaluator.py` | Precision/Recall/nDCG、空召回计分和缺失标注校验 |
| `scripts/evaluate.py` | Validate-only、产物目录、错误门禁、指标门槛、合成集显式授权 |
| `config/settings.yaml` | 默认启用五项确定性指标 |
| `tests/unit/test_eval_runner.py` | 集合、错误、伪答案、聚合和产物测试 |
| `tests/unit/test_custom_evaluator.py` | 新指标与去重测试 |
| `tests/unit/test_evaluate_cli.py` | CLI 门槛解析测试 |
| `tests/e2e/test_recall.py` | 删除 0 门槛，改为显式环境基线 |
| `tests/fixtures/SYNTHETIC_FIXTURES.md` | 标记合成占位集用途与限制 |

## 5. 验证结果

执行环境：仓库 `.venv`，Python 3.13.5。

已执行：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\unit\test_custom_evaluator.py `
  tests\unit\test_eval_runner.py -q

.\.venv\Scripts\python.exe -m pytest `
  tests\e2e\test_recall.py `
  tests\e2e\test_paper_golden_e2e.py -q

.\.venv\Scripts\python.exe -m pytest tests\unit -q
```

最终结果：

- 评测相关定向测试（含 E2E）：47 passed，2 skipped；两个 skip 是因为未配置真实 Hit/MRR 基线门槛，符合新设计。
- 完整单元测试（包含后续 collection 路由回归测试）：1330 passed，1 skipped。

另验证：

- 正式 object-form golden set 可通过 `--validate-only`。
- 旧式 list-form 合成集默认拒绝。
- 加 `--allow-legacy-synthetic` 后可用于显式 smoke validation。

测试仅出现既有 `tests/.tmp/pytest_cache` 无写权限警告，不影响测试结果。

## 6. 行为变化与迁移提醒

1. `--no-search` 现在是 `--validate-only` 的兼容别名，不再生成没有检索意义的质量报告。
2. 搜索初始化失败时 CLI 立即返回退出码 2，不再继续运行。
3. 没有真实 `answer_generator` 时不能运行有意义的 Ragas 生成评测。
4. 没有 `expected_chunk_ids` 的 CustomEvaluator case 会显示 `evaluation_error`。
5. list-form golden set 需要显式 `--allow-legacy-synthetic`。
6. 运行默认写入 `data/evaluation_runs`；临时调试可使用 `--no-save`。

## 7. 尚未包含在本轮的内容

- 页码/章节/span hash 的稳定引用定位符。
- 0–3 级 graded relevance 与 graded nDCG。
- 外部 Agent 工具调用 Trace 的自动采集。
- 80 条人工 Core Set 的实际标注。
- BM25-only、Dense-only、RRF 和 Rerank 的一键消融编排。
- 配对 bootstrap 置信区间和跨版本自动对比。

这些属于后续 P1/评测数据建设，不影响本轮 P0 可信化目标。

## 8. 验收结论

本轮目标已达到：评测失败不再静默、集合范围可验证、IR 指标足够形成第一版基线、运行结果可追溯、合成数据与正式数据有明确边界、CI 可以配置真实门槛。

下一步应先用现有索引语料复核 12 条结构化 case，再补齐缺失论文并扩充开发集；不应立即增加新的检索架构。

后续实际索引审计发现仓库当前只保留 3 篇研究论文；正式子集运行、历史 Trace 分析及 collection 路由修复见 [`EVALUATION_BASELINE_REPORT.md`](EVALUATION_BASELINE_REPORT.md)。

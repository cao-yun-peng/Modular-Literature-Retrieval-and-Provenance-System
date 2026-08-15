# RAG 评测系统使用手册

> 适用版本：P0 评测可信化改造后  
> 入口：`scripts/evaluate.py`  
> 默认正式测试集：`tests/fixtures/golden_test_set.json`

## 1. 你能用它做什么

当前评测系统主要回答三个问题：

1. 正确证据有没有被召回？
2. 正确证据排得是否足够靠前、覆盖是否完整？
3. 本次运行是否发生索引、模型、集合或 evaluator 错误？

如果接入真实外部 Agent 答案，还可以用 Ragas 评测生成忠实性。但默认 CLI 只负责真实检索评测，不会把 chunk 拼接成伪答案。

## 2. 环境准备

在项目根目录打开 PowerShell：

```powershell
Set-Location "E:\project\RAG_TEACHER\MODULAR-RAG-MCP-SERVER-main\MODULAR-RAG-MCP-SERVER-main"
```

后续命令统一使用项目虚拟环境：

```powershell
.\.venv\Scripts\python.exe --version
```

运行正式质量评测前必须满足：

- 文献已经摄取到目标 collection；
- Chroma 与对应 BM25 索引均存在；
- embedding 配置和密钥可用；
- golden case 的 chunk ID 来自当前索引版本。

如果要复用仓库已有的历史索引，它位于 `data/db`，请先设置：

```powershell
$env:MODULAR_RAG_DATA_DIR = (Resolve-Path data\db).Path
```

新环境可以把 `MODULAR_RAG_DATA_DIR` 指向自己的运行数据根目录。

## 3. 三种使用模式

### 3.1 只检查测试集格式

不加载模型和索引：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --validate-only `
  --test-set tests\fixtures\golden_test_set.json
```

适合标注过程中频繁检查 JSON。

`--no-search` 仍可使用，但只是 `--validate-only` 的兼容别名。

### 3.2 运行正式检索评测

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection default `
  --top-k 10
```

默认会把完整结果保存到 `data/evaluation_runs/<run_id>`。

### 3.3 机器可读 JSON 输出

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection default `
  --top-k 10 `
  --json
```

进度信息写到 stderr，报告 JSON 写到 stdout，便于 CI 或脚本解析。

## 4. Golden Set 格式

推荐格式：

```json
{
  "description": "10 篇论文人工复核集",
  "version": "2.0",
  "corpus_version": "papers-2026-08-14",
  "test_cases": [
    {
      "id": "fact_001",
      "query": "论文中如何定义……？",
      "query_type": "fact",
      "collection": "papers",
      "expected_chunk_ids": [
        "doc_xxx_0012_abcd1234"
      ],
      "expected_sources": [
        "paper-a.pdf"
      ],
      "reference_answer": "人工核验的参考答案"
    }
  ]
}
```

字段说明：

| 字段 | 是否必需 | 用途 |
|---|---|---|
| `id` | 推荐 | 稳定标识失败样本 |
| `query` | 必需 | 检索问题，不能为空 |
| `query_type` | 推荐 | 生成切片指标，例如 fact/table/cross_document |
| `collection` | 可选 | 覆盖命令行 collection |
| `expected_chunk_ids` | CustomEvaluator 必需 | 一个或多个正确证据块 |
| `expected_sources` | 推荐 | 人工复核来源，后续文档级指标使用 |
| `reference_answer` | 端到端评测推荐 | 外部 Agent 回答的参考答案 |

兼容旧字段 `question`、`supporting_chunk_ids`、`answer` 和 `query_id`，但新数据建议统一使用上表字段。

### 4.1 如何标注正确 chunk

1. 对每个真实问题执行一次检索。
2. 阅读 Top-20 结果及原 PDF。
3. 标出所有能够直接或必要地支撑答案的 chunk，而不只是当前排第一的 chunk。
4. 把 ID 写入 `expected_chunk_ids`。
5. 改变切分或重新摄取后，重新核对 ID。

当前 chunk ID 可能随切分配置改变，因此每次报告必须保留 golden set SHA256 和索引版本说明。

### 4.2 不允许用于正式成绩的数据

`tests/fixtures/paper_golden_set.json` 包含 `paper_chunk_001` 等占位 ID，是合成 smoke fixture。正式 loader 默认拒绝。

只有检查旧格式兼容性时才运行：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --validate-only `
  --allow-legacy-synthetic `
  --test-set tests\fixtures\paper_golden_set.json
```

这个结果不能写进简历、技术报告或性能对比。

## 5. 指标说明

默认配置位置：`config/settings.yaml`。

```yaml
evaluation:
  enabled: true
  provider: "custom"
  metrics:
    - "hit_rate"
    - "mrr"
    - "precision_at_k"
    - "recall_at_k"
    - "ndcg_at_k"
```

| 指标 | 含义 | 重点观察 |
|---|---|---|
| `hit_rate` | Top-K 中是否至少有一个正确 chunk | 快速判断能否找到证据 |
| `mrr` | 第一个正确 chunk 的倒数排名 | 正确证据是否排在前面 |
| `precision_at_k` | 返回结果中正确 chunk 的比例 | 噪声是否过多 |
| `recall_at_k` | 所有标注正确 chunk 中被找到的比例 | 多证据是否覆盖完整 |
| `ndcg_at_k` | 考虑位置折损的整体排序质量 | 综合比较排序方案 |
| `evaluation_success_rate` | 正常完成全部评测步骤的比例 | 指标是否可相信 |
| `retrieval_success_rate` | 检索基础设施正常工作的比例 | 索引和服务稳定性 |

K 由 `--top-k` 指定；如果直接调用 evaluator 且未传 `top_k`，才使用实际返回数量。

不要只报告一个 Hit Rate。推荐最少同时报告：Hit Rate、Recall@K、nDCG@K、MRR 和两个 success rate。

## 6. 设置真实质量门槛

### 6.1 CLI 门禁

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection papers `
  --top-k 10 `
  --fail-on-errors `
  --min-metric hit_rate=0.80 `
  --min-metric recall_at_k=0.70 `
  --min-metric ndcg_at_k=0.60
```

以下情况返回退出码 1：

- 任一指定 metric 未产生；
- 任一 aggregate metric 低于门槛；
- 使用 `--fail-on-errors` 且至少一个 query 不是 `success`。

门槛数字必须来自冻结测试集的已发布基线。第一次运行不要先定漂亮数字，应先记录真实结果，再以“不允许比基线下降超过约 2 个百分点”为初始规则。

### 6.2 Pytest 门禁

```powershell
$env:RAG_EVAL_MIN_HIT_RATE = "0.80"
$env:RAG_EVAL_MIN_MRR = "0.60"

.\.venv\Scripts\python.exe -m pytest `
  tests\e2e\test_recall.py -v
```

未设置环境变量时，对应质量 gate 会显示 skipped，不会再以阈值 0 假装通过。

## 7. 运行产物怎么看

目录示例：

```text
data/evaluation_runs/20260814T120000.123456Z/
├── manifest.json
├── aggregate_metrics.json
├── query_results.jsonl
└── report.json
```

### `manifest.json`

先看这里确认是否在比较同一实验：

- `test_set_sha256` 是否相同；
- `collection` 是否相同；
- `top_k` 是否相同；
- evaluator 是否相同；
- `status_counts` 是否全为 success。

### `aggregate_metrics.json`

用于版本总览和 CI 判断。对比两次运行时必须保持语料、索引、模型和配置一致。

### `query_results.jsonl`

一行一个 query，最适合筛选失败：

```powershell
Get-Content data\evaluation_runs\<run_id>\query_results.jsonl |
  Select-String 'retrieval_error|evaluation_error'
```

### `report.json`

完整机器可读报告，包含汇总和全部逐查询结果。

临时调试不想保存：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --no-save
```

自定义保存位置：

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --output-dir artifacts\evaluation_runs
```

## 8. 状态和错误处理

### `retrieval_error`

检查：

- collection 名称；
- Chroma 目录；
- 对应 BM25 index；
- embedding 服务和密钥；
- Dense/BM25 两路是否都不可用。

它不是普通 0 分，不应通过降低质量门槛解决。

### `evaluation_error`

CustomEvaluator 常见原因：

- case 没有 `expected_chunk_ids`；
- chunk 缺少可识别的 ID。

Ragas 常见原因：

- 没有传入真实 Agent 答案；
- Judge LLM/Embedding 配置不可用；
- Ragas 依赖或远程服务失败。

### `answer_generation_error`

只在 Python API 配置了真实 `answer_generator` 时出现。应检查外部 Agent 调用、超时和返回类型。

## 9. 接入外部 Agent 答案

检索指标和外部 Agent 生成指标应分开运行。下面展示 Python API 的接入方式：

```python
from src.observability.evaluation.eval_runner import EvalRunner


def generate_with_agent(query, retrieved_chunks):
    # 这里调用真实外部 Agent；必须返回最终答案文本。
    return external_agent.run(query=query, evidence=retrieved_chunks)


runner = EvalRunner(
    settings=settings,
    hybrid_search=hybrid_search,
    evaluator=ragas_evaluator,
    answer_generator=generate_with_agent,
)

report = runner.run(
    "tests/fixtures/golden_test_set.json",
    top_k=10,
    collection="papers",
)
report.save_artifacts("data/evaluation_runs")
```

注意：当前接口会让 Agent 使用传入的检索块生成答案，尚未自动采集 MCP Tool 调用全过程。要评估“Agent 是否会正确选择和调用四个 MCP Tools”，后续还需接入 Agent Trace。

## 10. 推荐日常流程

### 修改检索代码前

1. 冻结配置和 collection。
2. 运行当前版本，保存 baseline run ID。
3. 确认 `status_counts` 全部成功。

### 修改后

1. 使用完全相同命令重跑。
2. 对比 aggregate metrics 和 p95/耗时。
3. 从 `query_results.jsonl` 查看退化问题。
4. 给失败 case 标记原因：解析、切分、召回、排序、集合或 evaluator。
5. 只有证据明确时才保留改动。

### 每次正式对外报告

至少保存：

- Git commit SHA；
- corpus/index 版本；
- run 目录；
- golden set SHA256；
- 配置与模型版本；
- aggregate 和 query type 切片；
- 失败案例说明。

## 11. 当前 10 篇文献的建议执行顺序

1. 先复核 `golden_test_set.json` 中 12 条 case，确认 chunk ID 和原文一致。
2. 将你本人确认的 10 条问题标记稳定 ID 和 query type。
3. 跑一次 Top-10 baseline，不设置绝对门槛，只保存结果。
4. 人工检查所有 miss，排除标注错误和旧 chunk ID。
5. 形成第一版经复核 baseline 后再配置 `--min-metric`。
6. 扩充到 40–50 条开发集，再做 BM25/Dense/RRF/Rerank 消融。
7. 达到约 80 条后冻结 30 条测试集，调参过程中不查看测试集结果。

## 12. 快速命令清单

```powershell
# 格式检查
.\.venv\Scripts\python.exe scripts\evaluate.py --validate-only

# 正式评测并保存运行产物
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --collection papers --top-k 10

# JSON 输出
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --collection papers --top-k 10 --json

# 严格质量门禁
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --collection papers `
  --top-k 10 `
  --fail-on-errors `
  --min-metric hit_rate=0.80 `
  --min-metric ndcg_at_k=0.60

# 运行评测相关单元测试
.\.venv\Scripts\python.exe -m pytest `
  tests\unit\test_custom_evaluator.py `
  tests\unit\test_eval_runner.py `
  tests\unit\test_evaluate_cli.py -q
```

## 13. 当前基线

首份真实子集基线见 [`EVALUATION_BASELINE_REPORT.md`](EVALUATION_BASELINE_REPORT.md)。该报告只覆盖当前索引中的 3 篇论文，不代表 10 篇全量系统。

## 14. RRF 与 Cross-Encoder 消融

消融脚本只执行一次 Hybrid Search，并让 RRF 与 Cross-Encoder 共用同一组候选，避免把召回差异误算为重排收益。

### 14.1 安装依赖与首次下载模型

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rerank]"

.\.venv\Scripts\python.exe -c `
  "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

首次下载需要联网。下载完成后，正式评测建议加 `--offline`，防止 Hugging Face Hub 的版本探测影响离线复现。

### 14.2 运行消融

```powershell
$env:MODULAR_RAG_DATA_DIR = (Resolve-Path data\db).Path

.\.venv\Scripts\python.exe scripts\evaluate_retrieval_ablation.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection eval_test `
  --candidate-k 20 `
  --top-k 10 `
  --offline
```

参数说明：

| 参数 | 含义 |
|---|---|
| `--candidate-k` | RRF 提供给 Cross-Encoder 的共享候选池大小 |
| `--top-k` | 两种排序共同计算指标的截断深度，必须不大于 candidate-k |
| `--model` | Cross-Encoder 模型名称或本地模型目录 |
| `--offline` | 只从本地缓存加载模型，不访问 Hugging Face Hub |
| `--output-dir` | 不覆盖的版本化产物根目录 |
| `--no-save` | 只看终端结果，不保存产物 |

如果 Dense 或 BM25 任一路失败，脚本会退出并且不生成成功报告；Cross-Encoder 失败也不会回退为 RRF 后继续计分。

### 14.3 查看产物

```text
data/evaluation_ablation_runs/<run_id>/
├── manifest.json
├── summary.json
└── per_query.jsonl
```

- `manifest.json`：模型、依赖版本、K 值、collection 和测试集 SHA256；
- `summary.json`：两组汇总指标、差值、改善/持平/退化题数与延迟；
- `per_query.jsonl`：共享候选、两种完整顺序、证据排名、逐题指标和 rerank 分数。

当前实测结论见 [`RRF_CROSS_ENCODER_ABLATION_REPORT.md`](RRF_CROSS_ENCODER_ABLATION_REPORT.md)。

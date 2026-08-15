# 项目评估指南

> 如何系统性地评估 Modular RAG MCP Server 的检索质量

> **P0 改造提示（2026-08-14）**：评测执行器的最新行为、质量门禁和运行产物请以 [`docs/EVALUATION_USER_MANUAL.md`](docs/EVALUATION_USER_MANUAL.md) 为准；实施内容与兼容性变化见 [`docs/EVALUATION_P0_IMPLEMENTATION_REPORT.md`](docs/EVALUATION_P0_IMPLEMENTATION_REPORT.md)。本文保留为原有评测设计背景，其中 `--no-search`、默认 0 阈值和伪答案回退等旧说明已不再适用。

---

## 目录

- [评估体系概览](#评估体系概览)
- [快速上手：5 分钟跑通评估](#快速上手5-分钟跑通评估)
- [Golden Test Set 黄金测试集](#golden-test-set-黄金测试集)
  - [格式说明](#格式说明)
  - [如何构建测试集](#如何构建测试集)
  - [标注 expected_chunk_ids 的技巧](#标注-expected_chunk_ids-的技巧)
- [评估指标详解](#评估指标详解)
- [运行评估](#运行评估)
  - [CLI 命令行](#cli-命令行)
  - [Python API](#python-api)
  - [Pytest 回归测试](#pytest-回归测试)
- [多维度评估方案](#多维度评估方案)
  - [方案一：检索质量评估 (IR Metrics)](#方案一检索质量评估-ir-metrics)
  - [方案二：端到端生成质量 (RAGAS)](#方案二端到端生成质量-ragas)
  - [方案三：复合评估 (Composite)](#方案三复合评估-composite)
  - [方案四：论文检索专项评估](#方案四论文检索专项评估)
- [CI/CD 集成](#cicd-集成)
- [常见问题与调试](#常见问题与调试)
- [评估清单](#评估清单)

---

## 评估体系概览

本项目提供三层评估能力，覆盖从检索到生成的全链路：

```
┌──────────────────────────────────────────────────────┐
│                   评估体系架构                          │
│                                                       │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐  │
│  │ Golden Test │   │  Retrieval  │   │  Evaluator  │  │
│  │    Set      │   │  Pipeline   │   │  (Scorer)   │  │
│  │  (JSON)     │   │ HybridSearch│   │             │  │
│  │             │   │   / E2E     │   │             │  │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘  │
│         │                 │                 │          │
│         └─────────┬───────┘                 │          │
│                   │                         │          │
│           ┌───────▼────────┐                │          │
│           │   EvalRunner   │────────────────┤          │
│           │ (orchestrator) │                │          │
│           └───────┬────────┘                │          │
│                   │                         │          │
│         ┌─────────▼──────────┐   ┌──────────▼────────┐ │
│         │  CustomEvaluator   │   │  RagasEvaluator   │ │
│         │  hit_rate / MRR    │   │  faithfulness /   │ │
│         │  (无外部依赖)       │   │  answer_relevancy │ │
│         └────────────────────┘   └───────────────────┘ │
│                                                       │
│  入口:                                                 │
│  scripts/evaluate.py  (CLI)                            │
│  tests/e2e/test_recall.py  (CI/CD 回归)                 │
│  tests/e2e/test_paper_pipeline.py  (论文专项)            │
└──────────────────────────────────────────────────────┘
```

**三个评估入口**：

| 入口 | 用途 | 何时使用 |
|---|---|---|
| `scripts/evaluate.py` | 交互式评估 | 开发调试、手动验证 |
| `pytest -m e2e` | 自动化回归 | CI/CD 门禁 |
| Python API | 编程式评估 | 自定义脚本、批量实验 |

---

## 快速上手：5 分钟跑通评估

### 第一步：确保数据已摄入

```bash
# 至少摄入一些文档到知识库
python scripts/ingest.py --path papers/ --collection eval_test --paper-loader
```

### 第二步：准备测试集

创建 `my_golden_test.json`：

```json
{
  "description": "手动验证用测试集",
  "version": "1.0",
  "test_cases": [
    {
      "query": "什么是 nonreciprocal interaction？",
      "expected_chunk_ids": [],
      "reference_answer": "Nonreciprocal interactions 是指..."
    }
  ]
}
```

> 如果暂时没有 `expected_chunk_ids`，可以留空数组 — 此时只能验证"查询是否有结果返回"，不能计算 hit_rate/MRR。

### 第三步：运行评估

```bash
# 使用自定义测试集
python scripts/evaluate.py --test-set my_golden_test.json --collection eval_test

# 输出 JSON（便于程序处理）
python scripts/evaluate.py --test-set my_golden_test.json --json

# 跳过检索（仅测试评估逻辑本身）
python scripts/evaluate.py --test-set my_golden_test.json --no-search
```

### 第四步：查看结果

```
===========================================================
📊 评估报告
===========================================================
评估器: CustomEvaluator (hit_rate, mrr)
测试集: my_golden_test.json
查询数: 1
总耗时: 1.234s
===========================================================

📈 聚合指标:
  hit_rate:  0.0000   
  mrr:       0.0000   

───────────────────────────────────────────────────────────

🔍 查询详情:
───────────────────────────────────────────────────────────

查询 1: "什么是 nonreciprocal interaction？"
  返回数量: 8
  指标:
    hit_rate: 0.0000
    mrr: 0.0000
```

---

## Golden Test Set 黄金测试集

### 格式说明

```json
{
  "description": "测试集描述（可选）",
  "version": "1.0",
  "test_cases": [
    {
      "query": "查询问题",
      "expected_chunk_ids": ["doc_hash_0000_abc12345"],
      "expected_sources": ["papers/paper_name.pdf"],
      "reference_answer": "标准答案（用于 RAGAS 评估）"
    }
  ]
}
```

| 字段 | 必填 | 类型 | 说明 |
|---|---|---|---|
| `query` | ✅ | string | 测试查询 |
| `expected_chunk_ids` | | string[] | 预期召回 Chunk ID 列表（供 hit_rate/MRR 计算） |
| `expected_sources` | | string[] | 预期来源文件（软性检查） |
| `reference_answer` | | string | 标准答案（供 RAGAS faithfulness 等 LLM 评估） |

> **两种格式都支持**：也可以用 `question` / `supporting_chunk_ids` / `answer` 作为字段名。

### 如何构建测试集

**推荐流程**：

```
1. 收集真实查询 → 2. 人工标注相关 Chunk → 3. 写入 golden_test_set.json → 4. 持续迭代
```

**第一步：收集查询**

从以下来源收集代表性查询：
- 你自己使用时的真实问题
- 论文/文档中你最关心的知识点
- 预期的用户提问（短关键词 + 完整句子 + 专业术语）

```json
{
  "query": "nonreciprocal XY model topological defect annihilation",
  "expected_chunk_ids": [],
  "reference_answer": ""
}
```

**第二步：摄入文档并记录 Chunk ID**

```bash
# 摄入并查看生成了哪些 Chunk
python scripts/ingest.py --path papers/your_paper.pdf --collection my_eval --paper-loader --force

# 查看 ChromaDB 中的 Chunk
python -c "
from src.core.settings import load_settings
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
store = VectorStoreFactory.create(load_settings(), collection_name='my_eval')
results = store.collection.get(include=['metadatas', 'documents'])
for i, (cid, meta) in enumerate(zip(results['ids'], results['metadatas'])):
    ctype = meta.get('chunk_type', 'body')
    title = meta.get('title', '')[:60]
    print(f'{i}: {cid} [{ctype}] {title}')
"
```

**第三步：标注预期 Chunk**

对每个 query，运行检索，人工判断哪些 Chunk 真正相关：

```bash
python scripts/query.py --query "你的查询" --collection my_eval --verbose
```

记录相关的 `chunk_id` 填入 `expected_chunk_ids`。

### 标注 expected_chunk_ids 的技巧

1. **从少到多**：先标注 5-10 个 query，每个 1-3 个相关 Chunk
2. **覆盖多样性**：包含不同难度（精确匹配 vs 语义理解）、不同文档、不同主题
3. **定期更新**：新文档摄入后，补充对应的测试 query
4. **利用 title_abstract chunk**：论文模式下，标题+摘要 Chunk 是最可靠的"文档级别"召回目标

推荐的测试集规模：

| 阶段 | Queries | 标注 Chunks | 用途 |
|---|---|---|---|
| **起步** | 5-10 | 每 query 1-2 个 | 冒烟测试 |
| **迭代** | 20-50 | 每 query 2-5 个 | 开发回归 |
| **发布** | 50-200+ | 充分标注 | CI 门禁 |

---

## 评估指标详解

### hit@k (Hit Rate)

**定义**：Top-K 结果中是否包含至少一个预期 Chunk。

$$
\text{hit@k} = \begin{cases} 1 & \text{if } |\text{retrieved}_k \cap \text{expected}| > 0 \\ 0 & \text{otherwise} \end{cases}
$$

**含义**：衡量"**能不能找到**"——这是最基本的召回能力指标。

**适用场景**：
- 验证新文档摄入后能被检索到
- 论文专项：验证 "nonreciprocal" 能召回对应论文

### MRR (Mean Reciprocal Rank)

**定义**：第一个相关结果的排名的倒数的平均值。

$$
\text{MRR} = \frac{1}{|Q|} \sum_{i=1}^{|Q|} \frac{1}{\text{rank}_i}
$$

其中 `rank_i` 是第 i 个查询的第一个相关结果的排名（1-based）。

**含义**：衡量"**找得多靠前**"——不仅找到，还要排在前面。

**示例**：

| Query | 第一个相关结果排名 | RR |
|---|---|---|
| Q1 | 第 1 位 | 1.0 |
| Q2 | 第 3 位 | 0.33 |
| Q3 | 未找到 | 0.0 |
| **MRR** | | **(1.0+0.33+0.0)/3 = 0.44** |

### 指标选择建议

| 场景 | 推荐指标 | 说明 |
|---|---|---|
| 新数据刚入库 | hit@k | 先确认能搜到 |
| 调优检索参数 | MRR | 看排名改善 |
| 对比不同 Embedding | hit@k + MRR | 综合评估 |
| 论文检索 | hit@k (source 级别) | 论文粒度召回 |
| LLM 生成质量 | faithfulness | 需要 RAGAS |

---

## 运行评估

### CLI 命令行

```bash
# 基本用法
python scripts/evaluate.py \
  --test-set tests/fixtures/golden_test_set.json \
  --collection default \
  --top-k 10

# 输出 JSON（便于脚本处理）
python scripts/evaluate.py --test-set my_set.json --json > report.json

# 只看评估逻辑（不连检索引擎）
python scripts/evaluate.py --test-set my_set.json --no-search

# 论文专项评估
python scripts/evaluate.py \
  --test-set tests/fixtures/golden_test_set.json \
  --collection research_papers \
  --top-k 20
```

### Python API

```python
from src.core.settings import load_settings
from src.observability.evaluation.eval_runner import EvalRunner, load_test_set
from src.libs.evaluator.evaluator_factory import EvaluatorFactory
from src.core.query_engine.hybrid_search import create_hybrid_search

# 1. 加载配置
settings = load_settings()

# 2. 创建评估器
evaluator = EvaluatorFactory.create(settings)

# 3. 创建检索引擎（需要先初始化各个组件）
# ... 或使用 create_hybrid_search() 工厂函数

# 4. 运行评估
runner = EvalRunner(settings, hybrid_search, evaluator)
report = runner.run("my_golden_test.json", top_k=10, collection="my_docs")

# 5. 查看结果
print(f"hit_rate: {report.aggregate_metrics['hit_rate']:.4f}")
print(f"MRR: {report.aggregate_metrics['mrr']:.4f}")

for qr in report.query_results:
    print(f"  {qr.query[:40]}... hit={qr.metrics.get('hit_rate', 0):.0f}")
```

### Pytest 回归测试

```bash
# 运行 E2E 召回回归（需要已索引数据）
pytest tests/e2e/test_recall.py -v -m e2e

# 运行论文专项 E2E 测试
pytest tests/e2e/test_paper_pipeline.py -v -m e2e

# 在 CI 中设置阈值门禁
# 编辑 tests/e2e/test_recall.py 中的阈值：
#   HIT_RATE_THRESHOLD = 0.7   # 70% hit@10
#   MRR_THRESHOLD = 0.3        # MRR > 0.3
```

---

## 多维度评估方案

### 方案一：检索质量评估 (IR Metrics)

**适用场景**：日常开发、参数调优、组件替换对比

**配置** (`config/settings.yaml`)：
```yaml
evaluation:
  enabled: true
  provider: "custom"
  metrics:
    - "hit_rate"
    - "mrr"
```

**运行**：
```bash
python scripts/evaluate.py --test-set tests/fixtures/golden_test_set.json --top-k 10
```

**A/B 对比流程**：
```bash
# 1. 基线：当前配置
python scripts/evaluate.py --test-set my_set.json --json > baseline.json

# 2. 修改配置（如换 Embedding 模型、调 RRF k值）
# 编辑 config/settings.yaml

# 3. 重新评估
python scripts/evaluate.py --test-set my_set.json --json > experiment.json

# 4. 对比
python -c "
import json
b = json.load(open('baseline.json'))
e = json.load(open('experiment.json'))
print(f'Baseline hit_rate: {b[\"aggregate_metrics\"][\"hit_rate\"]:.4f}')
print(f'Experiment hit_rate: {e[\"aggregate_metrics\"][\"hit_rate\"]:.4f}')
"
```

### 方案二：端到端生成质量 (RAGAS)

**适用场景**：评估最终回答质量（需要 LLM API）

**前置条件**：
```bash
pip install ragas
```

**配置**：
```yaml
evaluation:
  enabled: true
  provider: "ragas"
  metrics:
    - "faithfulness"       # 答案是否忠实于上下文
    - "answer_relevancy"   # 答案是否与问题相关
    - "context_precision"  # 上下文是否精确
```

**运行**：
```bash
python scripts/evaluate.py --test-set golden_with_answers.json --top-k 10
```

> 注意：RAGAS 需要 `reference_answer` 或使用 LLM-as-Judge 自动评分。确保 `golden_test_set.json` 中每条有 `reference_answer` 字段，或者依赖 RAGAS 内置的 LLM judge。

### 方案三：复合评估 (Composite)

**适用场景**：同时运行多个评估器，综合打分

**配置**：
```yaml
evaluation:
  enabled: true
  provider: "composite"
  backends:
    - "custom"
    - "ragas"
  metrics:
    - "hit_rate"
    - "mrr"
    - "faithfulness"
```

这样一次运行即可获得检索指标 + 生成质量指标的综合报告。

### 方案四：论文检索专项评估

**适用场景**：验证学术论文的 GROBID 解析 + 连带召回效果

**测试集设计**：针对论文场景设计专门的 query 类型：

```json
{
  "description": "论文检索专项测试集",
  "version": "1.0",
  "test_cases": [
    {
      "query": "nonreciprocal interactions reshape topological defect",
      "expected_chunk_ids": [],
      "expected_sources": ["Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf"]
    },
    {
      "query": "XY model phase diagram",
      "expected_chunk_ids": [],
      "expected_sources": ["Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf"]
    },
    {
      "query": "Figure 1 shows the annihilation process",
      "expected_chunk_ids": [],
      "expected_sources": ["Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf"]
    }
  ]
}
```

**评估维度**：

| 维度 | 指标 | 说明 |
|---|---|---|
| **文档级召回** | hit@k (source) | 论文是否出现在 Top-K 结果中 |
| **标题+摘要召回** | hit@k (chunk_type) | 标题摘要块是否被召回 |
| **图表连带召回** | `has_linked_assets` | 正文块是否触发了图表连带 |
| **元数据完整性** | DOI / authors 存在性 | 召回块的元数据是否完整 |

**运行**：
```bash
# 先摄入论文
python scripts/ingest.py --path papers/ --collection paper_eval --paper-loader --force

# 运行论文专项 E2E 测试
pytest tests/e2e/test_paper_pipeline.py -v -m e2e

# 自定义论文评估脚本
python scripts/evaluate.py --test-set paper_golden_set.json --collection paper_eval --top-k 20
```

---

## CI/CD 集成

### GitHub Actions 示例

```yaml
# .github/workflows/eval.yml
name: RAG Evaluation

on:
  pull_request:
    paths:
      - 'src/**'
      - 'config/**'

jobs:
  recall-regression:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4

      - uses: actions/setup-python@v5
        with:
          python-version: '3.10'

      - name: Install dependencies
        run: pip install -e ".[dev]"

      - name: Run recall regression
        run: pytest tests/e2e/test_recall.py -v -m e2e
        env:
          AZURE_OPENAI_API_KEY: ${{ secrets.AZURE_OPENAI_API_KEY }}

      - name: Run paper pipeline E2E
        run: pytest tests/e2e/test_paper_pipeline.py -v -m e2e
        env:
          AZURE_OPENAI_API_KEY: ${{ secrets.AZURE_OPENAI_API_KEY }}
```

### 设置门禁阈值

编辑 `tests/e2e/test_recall.py`：

```python
# 随着数据质量和检索质量提升，逐步提高阈值
HIT_RATE_THRESHOLD = 0.7   # 70% 的查询至少召回一个相关结果
MRR_THRESHOLD = 0.3        # 平均第一个相关结果排在前 3 位
```

**渐进式阈值策略**：

| 阶段 | hit@10 | MRR | 说明 |
|---|---|---|---|
| **起步** | 0.0 | 0.0 | 无数据，不设卡 |
| **有标注数据** | 0.5 | 0.2 | 基本可检索 |
| **调优后** | 0.7 | 0.3 | 多数查询能命中 |
| **生产级** | 0.85 | 0.5 | 高质量检索 |

---

## 常见问题与调试

### Q1: 所有指标都是 0.0？

**原因**：`expected_chunk_ids` 为空或 Chunk ID 不匹配。

**排查**：
```bash
# 1. 确认 Chunk ID 格式正确
python -c "
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
from src.core.settings import load_settings
store = VectorStoreFactory.create(load_settings(), collection_name='your_collection')
results = store.collection.get(include=['metadatas'], limit=5)
for cid in results['ids']:
    print(cid)
"

# 2. 确认测试集中的 expected_chunk_ids 与 ChromaDB 中的一致
```

### Q2: 查询返回空结果？

**原因**：ChromaDB 中没有数据，或集合名称不匹配。

**排查**：
```bash
# 确认集合中有数据
python -c "
from src.core.settings import load_settings
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
store = VectorStoreFactory.create(load_settings(), collection_name='your_collection')
print(f'Chunk count: {store.collection.count()}')
"

# 列出所有集合
python -c "
from src.core.settings import load_settings
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
store = VectorStoreFactory.create(load_settings())
client = store.client
for col in client.list_collections():
    print(f'  {col.name}: {col.count()} chunks')
"
```

### Q3: RAGAS 评估报错？

**检查清单**：
- [ ] `pip install ragas` 已安装
- [ ] LLM API key 已配置
- [ ] `reference_answer` 字段已填写（或使用 LLM judge 模式）
- [ ] RAGAS 版本兼容（项目适配 ragas >= 0.4）

### Q4: hit_rate 波动大？

**原因**：Embedding 模型可能产生随机性，或测试集太小。

**建议**：
- 扩大测试集（至少 20+ queries）
- 多次运行取平均
- 关注趋势变化而非绝对值

---

## 评估清单

进行完整评估时，按以下清单逐项检查：

### 基础检查

- [ ] `python scripts/evaluate.py` 可以正常运行（无报错）
- [ ] 测试集 JSON 格式正确，至少 5 条 query
- [ ] 已摄入文档对应的 Chunk 存在于 ChromaDB 中

### 检索质量

- [ ] hit@10 已计算（所有 query）
- [ ] MRR 已计算（所有 query）
- [ ] 对比了不同 `top_k`（5 / 10 / 20）的影响
- [ ] 对有 `expected_chunk_ids` 的 query，hit_rate > 0

### 论文专项

- [ ] 论文模式摄入后，title_abstract chunk 存在
- [ ] figure / table chunk 存在且可独立检索
- [ ] 正文块包含 `linked_figures` / `linked_tables`
- [ ] `pytest tests/e2e/test_paper_pipeline.py -m e2e` 通过
- [ ] 查询论文关键词能召回对应论文

### 回归门禁

- [ ] `pytest tests/e2e/test_recall.py -m e2e` 通过
- [ ] 核心 query 的 hit_rate 不低于上次基线
- [ ] 新功能/新配置未导致已有指标下降

### 迭代优化

- [ ] 新增了本次摄入文档对应的测试 query
- [ ] 标注了部分 `expected_chunk_ids`（目标：>50% query 有标注）
- [ ] 记录了当前的指标基线（JSON 报告存档）

---

> **下一步**：当你完成了第一轮评估后，将基线报告保存下来，每次修改配置或代码后重新运行，对比指标变化。建议将 `evaluate.py` 加入 CI 流水线，在每次 PR 时自动运行召回回归测试。

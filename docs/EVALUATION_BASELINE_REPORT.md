# 文献 RAG 首份可复现检索基线报告

> 日期：2026-08-14  
> 正式 Run ID：`20260814T150811.591206Z`  
> 评测层级：Hybrid Search 组件级检索，不包含 Rerank 和外部 Agent 生成  
> 结论适用范围：当前 `eval_test` collection 中的 3 篇论文

## 1. 结论摘要

在完成 collection 路由修复和一条错误 golden 标注修正后，当前 3 篇论文子集的正式 Top-10 基线为：

| 指标 | 结果 |
|---|---:|
| Hit@10 | 1.0000 |
| Recall@10 | 0.9167 |
| MRR@10 | 0.4856 |
| nDCG@10 | 0.5648 |
| Precision@10 | 0.1083 |
| Retrieval Success Rate | 1.0000 |
| Evaluation Success Rate | 1.0000 |
| 查询数 | 12 |
| 总运行时间 | 8.954 s |

这说明 12 个问题都至少召回了一条正确证据，但部分正确证据排名偏后，且两个多证据问题没有覆盖全部标注证据。当前主要矛盾已经从“能否召回”转为“排序质量和多证据覆盖”。

该成绩不能代表用户所说的 10 篇论文全量系统：当前仓库和索引实际只有 3 篇研究论文。

## 2. 数据与索引审计

### 2.1 当前研究语料

仓库 `papers/research` 只有以下 3 篇 PDF：

1. `LiTenWoldeCaptionMoviesSIReSub_2(1).pdf`
2. `Nonreciprocal Interactions Reshape Topological Defect Annihilation.pdf`
3. `opathalage-et-al-2019-self-organized-dynamics-and-the-transition-to-turbulence-of-confined-active-nematics.pdf`

### 2.2 Collection 状态

| Collection | Chunk 数 | 研究文档数 | Golden ID 覆盖 |
|---|---:|---:|---:|
| `eval_test` | 139 | 3 | 15/15 |
| `eval_test1` | 139 | 3 | 15/15 |

两个 collection 当前具有相同 chunk 数和 golden ID 覆盖。正式 baseline 选择 `eval_test`。

Chroma 和 BM25 的历史索引位于 `data/db`。当前配置将 `MODULAR_RAG_DATA_DIR` 视为可覆盖的运行根目录，因此运行历史索引时显式设置：

```powershell
$env:MODULAR_RAG_DATA_DIR = (Resolve-Path data\db).Path
```

### 2.3 测试集

- 文件：`tests/fixtures/golden_test_set.json`
- 测试问题：12 条
- 唯一人工证据 chunk：15 个
- 正式运行测试集 SHA256：`d8606e9021a2fc610e5ab55b7319e2e3cf9844b38a2eca3a781e80f71436b8a7`

注意：SHA256 对应修正后的测试集，后续任何标注变化都会产生新的基线版本。

## 3. 本次发现并修复的问题

### 3.1 Collection 被当成 metadata 重复过滤

第一次运行虽然索引和 golden ID 都存在，但 12 条查询全部返回 0 个 chunk。

原因是 `collection` 同时承担了两个语义：

- Sparse Retriever 用它选择 BM25 index；
- Hybrid Search 又把它传给 Dense metadata filter 和 post-filter。

现有 chunk metadata 并不保证包含 `collection` 字段，而 Dense Retriever 已绑定到具体 Chroma collection，所以结果被错误清空。

修复后：

- `collection` 只作为 Sparse index 路由键；
- Dense 和 post-filter 只接收真正的 metadata filters；
- `doc_type`、tags、source 等正常 metadata filter 行为不变。

新增单元测试验证：没有 `collection` metadata 的合法 chunk 不会被过滤，同时 Sparse Retriever 仍收到正确 collection。

### 3.2 第 12 条 Golden 标注串线

问题内容是弱约束条件下的 active nematics，但原 `expected_chunk_ids` 指向 vesicle/FFS 文档。由于问题与证据来自不同论文，原始 Hit=0 不能归因于检索器。

经过原文核对，修正为三个 active-nematics 证据块，分别支持：

- 缺陷对数量在 2–10 之间波动，仍保持相干环流；
- `Φ=±1` 表示系统尺度环流，并会在较长时间尺度上反转；
- 新缺陷沿已有环流方向重排并产生稳定环流的 active stress。

修正后第 12 条：Hit@10=1.0、Recall@10=0.3333，但首条正确证据只排在第 9，说明仍有真实排序优化空间。

## 4. 正式运行信息

运行命令：

```powershell
$env:MODULAR_RAG_DATA_DIR = (Resolve-Path data\db).Path

.\.venv\Scripts\python.exe scripts\evaluate.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection eval_test `
  --top-k 10 `
  --fail-on-errors `
  --output-dir data\evaluation_runs
```

产物目录：

```text
data/evaluation_runs/20260814T150811.591206Z/
├── manifest.json
├── aggregate_metrics.json
├── query_results.jsonl
└── report.json
```

## 5. 主题切片结果

| 主题切片 | 问题数 | Hit@10 | Recall@10 | MRR@10 | nDCG@10 | 平均耗时 |
|---|---:|---:|---:|---:|---:|---:|
| Active vesicle | 4 | 1.0000 | 0.9167 | 0.6750 | 0.6636 | 809.7 ms |
| Nonreciprocal XY | 4 | 1.0000 | 1.0000 | 0.3958 | 0.5481 | 712.4 ms |
| Active nematics | 4 | 1.0000 | 0.8333 | 0.3861 | 0.4826 | 715.3 ms |

Active nematics 是当前最弱切片。所有问题都有命中，但排序位置和多证据覆盖较弱。

## 6. 主要失败与低质量案例

### 多证据覆盖不足

- FFS 问题：3 条标注证据只召回 2 条，Recall@10=0.6667。
- 弱约束 active nematics：3 条标注证据只召回 1 条，Recall@10=0.3333。

### 正确证据排名偏后

- 泳压与曲率问题：首个正确证据排名第 5，MRR=0.2。
- 中等约束双周期问题：首个正确证据排名第 10，MRR=0.1。
- 弱约束 active nematics：首个正确证据排名第 9，MRR≈0.1111。

因此下一轮实验应该优先观察：Rerank 能否把已经召回的正确块向前移动，以及父子块/邻块扩展能否补齐多证据。

Precision@10 较低部分来自标注方式：多数问题只标 1 个直接证据，而系统固定返回 10 个候选。该指标可用于同一测试集上的相对比较，但不宜单独解释为“90% 结果无用”，因为目前没有对所有辅助相关块完成 0–3 级标注。

## 7. 历史 Trace 分析

历史文件：`logs/traces.jsonl`，共 41 条有效记录、0 条损坏记录。

| 项目 | 统计 |
|---|---:|
| Ingestion Trace | 19 |
| Query Trace | 22 |
| 唯一历史查询 | 8 |
| 历史加载文档 ID | 6 |
| 显式错误 | 0 |

历史 Query Trace 的 collection 分布：

- `e2e_paper_pipeline_test`：21 条
- `default`：1 条

它们不是本次 `eval_test` 的人工质量样本，因此只用于运行性能参考。

阶段耗时：

| 阶段 | 样本数 | 中位数 | 历史 P95 | 最大值 |
|---|---:|---:|---:|---:|
| Query processing | 22 | 0.07 ms | 10.79 ms | 20.47 ms |
| Sparse retrieval | 22 | 22.66 ms | 82.03 ms | 555.79 ms |
| Dense retrieval | 22 | 864.05 ms | 1387.53 ms | 4154.59 ms |
| Fusion | 22 | 0.15 ms | 0.20 ms | 0.21 ms |
| LLM Rerank | 1 | 6280.31 ms | 样本不足 | 6280.31 ms |

正式 baseline 的稳态单查询平均约 746 ms，与历史 Dense 中位数数量级一致。第一次冷启动运行首条查询约 60 s，并触发并行检索 30 s 超时；Ollama 模型预热后恢复到约 0.7–1.1 s。生产部署应增加模型预热，并把检索 timeout 做成配置项。

历史 Rerank 只有一个样本，约增加 6.28 s，不能据此下定论，但足以说明必须同时比较质量收益和延迟成本。

## 8. 当前结果不能证明什么

- 不能证明 10 篇论文上的整体效果，因为当前只有 3 篇。
- 不能证明外部 Agent 最终报告正确，因为本轮没有生成答案。
- 不能证明 Rerank 效果，因为当前 Eval CLI 运行的是 Hybrid Search 组件级结果。
- 不能证明图表、多跳或跨论文综合能力，因为 12 条问题集中在三个主题，且缺少明确 query type。
- 不能把 Hit@10=1.0 表述为“系统准确率 100%”。

## 9. 下一步优先级

### P1-A：先补齐语料与评测身份

1. 找回或重新摄取其余 7 篇论文。
2. 建立 corpus manifest，记录 PDF SHA256、collection、chunk 配置和 embedding 模型。
3. 为 12 条 case 增加 `id` 和 `query_type`。
4. 扩充到至少 40 条开发集后再调参。

### P1-B：做最小排序消融

在同一个测试集上比较：

1. BM25-only；
2. Dense-only；
3. RRF；
4. RRF + Cross-Encoder Rerank；
5. RRF + 父子块/邻块扩展。

重点 Gate：Hit@10 不下降，MRR/nDCG 提升，Active Nematics Recall@10 提升，同时报告 p95 延迟。

### P1-C：冷启动与 Trace

- 服务启动时预热 `nomic-embed-text`。
- 将检索超时改为配置项，并区分 timeout 与普通 branch fallback。
- 评测运行接入 Trace，保存 Dense/Sparse/RRF/Rerank 的逐阶段结果。

## 10. 推荐对外表述

当前可以严谨表述为：

> 在 3 篇论文、12 条人工问题、15 个标注证据块的初始测试集上，Hybrid Search 在 Top-10 取得 Hit Rate 1.00、Recall 0.917、MRR 0.486 和 nDCG 0.565；所有查询均成功完成。该结果为组件级初始基线，尚未覆盖 10 篇全量语料、Rerank 和外部 Agent 最终报告。

这种表述准确反映现有证据，也保留了后续提升空间。

## 11. 后续排序消融

同一批 RRF 候选上的 Cross-Encoder 实验已完成，详见 [`RRF_CROSS_ENCODER_ABLATION_REPORT.md`](RRF_CROSS_ENCODER_ABLATION_REPORT.md)。结果显示 MRR/nDCG 提升，但 Hit@10/Recall@10 下降，因此暂不替换默认 RRF 排序。

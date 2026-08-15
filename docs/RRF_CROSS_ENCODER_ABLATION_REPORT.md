# RRF 与 Cross-Encoder Rerank 消融报告

> 日期：2026-08-14  
> Run ID：`20260814T153622.616886Z`  
> 实验类型：同候选集排序消融  
> 结论范围：`eval_test` collection，3 篇论文、12 条人工问题、15 个证据块

## 1. 结论

Cross-Encoder 明显改善了首条相关证据的排名，但损害了 Top-10 的命中与证据覆盖，因此当前**不建议直接替换生产默认 RRF 排序**。

| 指标 | RRF | RRF 候选 + Cross-Encoder | 变化 |
|---|---:|---:|---:|
| Hit@10 | 1.0000 | 0.9167 | -0.0833 |
| Recall@10 | 0.9167 | 0.8611 | -0.0556 |
| MRR@10 | 0.4856 | 0.6227 | +0.1370 |
| nDCG@10 | 0.5648 | 0.6779 | +0.1131 |
| Precision@10 | 0.1083 | 0.1083 | 0.0000 |

Cross-Encoder 使 4/12 条问题的 MRR 改善、3/12 退化；使 5/12 条问题的 nDCG 改善、3/12 退化。排序收益是真实的，但尚未满足本项目设定的“Hit@10 不下降”安全门槛。

## 2. 公平实验设计

每条问题只执行一次 Hybrid Search：

1. Dense 与 BM25 各自召回，RRF 融合出同一组 Top-20 候选；
2. RRF 组直接取原顺序 Top-10；
3. Cross-Encoder 对同一组 20 个候选全部打分，再取重排 Top-10；
4. 两组使用完全相同的 `expected_chunk_ids` 和 CustomEvaluator；
5. 任一 Dense/BM25 分支失败、候选不足、模型异常或输出混入候选集外条目时，整次实验失败，不允许把 fallback 当成 Cross-Encoder 结果。

因此本实验只测量“排序器改变顺序”的影响，不混入二次召回、不同候选集或生成模型的影响。

固定参数：

| 项目 | 值 |
|---|---|
| Collection | `eval_test` |
| Candidate K | 20 |
| Evaluation K | 10 |
| RRF k | 60 |
| Dense / Sparse Top-K | 20 / 20 |
| Embedding | Ollama `nomic-embed-text` |
| Cross-Encoder | `cross-encoder/ms-marco-MiniLM-L-6-v2` |
| sentence-transformers | 5.7.0 |
| torch | 2.13.0 |
| transformers | 5.15.0 |
| Python | 3.13.5 |
| 运行设备 | Windows 11，AMD64 CPU |
| Golden SHA256 | `d8606e9021a2fc610e5ab55b7319e2e3cf9844b38a2eca3a781e80f71436b8a7` |

本次没有启用 GPU。配置文件中的生产 rerank provider 没有被修改；消融脚本在内存中显式覆盖为 `cross_encoder`，防止实验配置污染服务默认行为。

## 3. 延迟结果

| 延迟 | RRF 检索 | Cross-Encoder 新增耗时 |
|---|---:|---:|
| 总耗时（12 题） | 9.688 s | 13.016 s |
| 单题中位数 | 822.5 ms | 950.9 ms |
| 单题 P95 | 945.6 ms | 1786.3 ms |
| 单题平均新增 | — | 1084.7 ms |

Cross-Encoder 使本轮总处理时间由仅检索约 9.69 秒增加到约 22.70 秒，即增加约 134% 的检索阶段耗时。相较历史 Trace 中唯一一次 LLM Rerank 的 6.28 秒样本，本地 Cross-Encoder 更快，但仍不是零成本组件。

首次使用还需要下载模型权重；权重缓存后可用 `--offline` 阻止模型库联网探测，保证受限环境下可复现。

## 4. 逐题变化

主要改善：

| Case | 证据排名 RRF → Cross-Encoder | 影响 |
|---:|---:|---|
| 1 泳压与曲率 | 5 → 1 | MRR +0.8000，nDCG +0.6131 |
| 4 FFS | 1/6/14 → 1/2/3 | 三条证据全部进入 Top-3，Recall@10 从 2/3 提升到 3/3 |
| 5 非互易相互作用定义 | 4 → 2 | MRR +0.2500 |
| 7 缺陷湮灭动力学 | 2 → 1 | MRR +0.5000 |
| 8 与 Toner-Tu 的关系 | 2 → 1 | MRR +0.5000 |

主要退化：

| Case | 证据排名 RRF → Cross-Encoder | 影响 |
|---:|---:|---|
| 6 `+1` 缺陷形状 | 3 → 9 | MRR -0.2222 |
| 9 中等约束双周期 | 10 → 12 | 被挤出 Top-10，造成唯一一次 Hit@10 丢失 |
| 11 实验与理论差异 | 3 → 4 | MRR -0.0833 |

Case 12 的三条弱约束 active-nematics 证据中，两条没有进入原始 Top-20，第三条在两种排序下都位于第 9。Cross-Encoder 只能重排已召回候选，无法修复候选池外的召回缺失。这一问题应从查询扩展、Dense/BM25 参数、邻块/父子块扩展或摄取切分处理，而不是继续调 Cross-Encoder。

## 5. 上线判断

当前建议保留 RRF 为默认结果排序，Cross-Encoder 保持可选实验能力，理由如下：

- MRR 和 nDCG 的提升足够大，说明 Cross-Encoder 有继续优化价值；
- Hit@10 与 Recall@10 下降，不满足“引用证据不能丢”的文献溯源系统约束；
- 只有 12 条问题，单个 case 就会使 Hit 变化 8.33 个百分点，样本方差很大；
- CPU P95 新增约 1.79 秒，是否值得需要结合外部 Agent 总延迟预算判断。

下一轮优先实验：

1. 扩充到至少 40–50 条开发集，并为问题增加 query type；
2. 尝试 RRF 分数与 Cross-Encoder 分数的归一化加权，而不是完全覆盖 RRF 顺序；
3. 对排序方案增加硬门槛：Hit@10、Recall@10 不低于 RRF，再比较 MRR/nDCG；
4. 单独做 Active Nematics 召回改造，因为 Case 12 的缺失发生在 rerank 之前；
5. 在冻结测试集上只做最终确认，避免用 12 条当前样本反复调参导致过拟合。

## 6. 复现命令与产物

安装可选依赖并准备模型：

```powershell
.\.venv\Scripts\python.exe -m pip install -e ".[rerank]"

.\.venv\Scripts\python.exe -c `
  "from sentence_transformers import CrossEncoder; CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')"
```

执行消融：

```powershell
$env:MODULAR_RAG_DATA_DIR = (Resolve-Path data\db).Path

.\.venv\Scripts\python.exe scripts\evaluate_retrieval_ablation.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection eval_test `
  --candidate-k 20 `
  --top-k 10 `
  --offline
```

正式产物目录：

```text
data/evaluation_ablation_runs/20260814T153622.616886Z/
├── manifest.json
├── summary.json
└── per_query.jsonl
```

`manifest.json` 用于核对实验身份，`summary.json` 用于版本比较，`per_query.jsonl` 保留完整 RRF/Cross-Encoder 候选顺序、证据排名、模型分数、逐题指标和延迟。

## 7. 可对外使用的严谨表述

> 在当前 3 篇论文、12 条人工问题的初始检索集上，使用同一组 RRF Top-20 候选进行 Cross-Encoder 重排后，MRR@10 从 0.486 提升至 0.623，nDCG@10 从 0.565 提升至 0.678；但 Hit@10 从 1.000 降至 0.917、Recall@10 从 0.917 降至 0.861，CPU 单题中位新增延迟约 0.95 秒。因此该结果证明重排具有排序收益，但尚不足以支持默认上线，仍需扩大人工测试集并优化召回保持策略。

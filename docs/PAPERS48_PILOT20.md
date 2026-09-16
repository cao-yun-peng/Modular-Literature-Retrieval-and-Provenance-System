# 冻结 48 篇论文的 20 题检索测评

2026-09-16 新建，基于 `papers48-20260916` 的 48 篇原始 PDF、993 个新分块。
旧版问题、旧分块和旧 MRR/nDCG 不作为本批答案或成绩。

## 使用入口

- `data/paper_benchmark/papers48-pilot20-v1/QUESTIONS.md`：20 题、参考答案和证据位置，适合先通读。
- 同目录 `review.html`：完整原文、PDF 物理页链接、审核人、审核意见和 JSON 导出。
- 同目录 `dataset.json`：机器可读题目、26 组证据、分块 ID、精确字符区间、PDF 源片段和审核状态。
- `scripts/pilot_benchmark.py`：校验、审核导入、BM25/Dense/RRF 运行与离线重放。

题目由助手依据原文编写，尚未经过人工审核；全部是开发样本，不能称为独立测试集。
包含 14 条中文、6 条英文；机制 5 条、精确实体 3 条、方法条件 5 条、结果与局限 4 条、跨论文 3 条。
已知答案依据来自 19 篇论文，检索时始终面对完整 48 篇，题目没有来源文档过滤器。
这 20 条均可在已标注证据中回答。本批不测无答案识别、全文 Handoff、生成答案质量或 MCP/service 接口。

## 证据及审核规则

每题包含必需证据组；需要多篇论文或多个论点时，必须分别覆盖各组。组内的 `alternatives` 表示任选一条充分方案，每个方案中的片段则必须全部找到。

26 个分块片段均通过 ID、来源文档、字符偏移和逐字文本校验。26 个片段均有可定位的原始 PDF 页面片段，其中 20 个在规范化空白/断行后完整匹配，6 个仅有部分连续文本匹配。部分匹配仅为核对线索，不证明整段解析正确。程序不模糊匹配数字、符号或否定词来自动给予检索命中。

目前 20 题全部为 `pending`。人工确认问题自然、答案准确、证据充分后，在 HTML 中填审核人、勾选已核对来源、点击通过，再下载审核 JSON。不同意见使用“需修改”及备注。重新审核或修改问题需要保存新版本，保留旧稿和运行记录。

审核页的 `file://` 地址被自动浏览器 URL 策略阻止预览；文件已生成并通过脚本语法检查，但未完成浏览器交互/视觉验证。可在本机浏览器手动打开，也可先阅读 `QUESTIONS.md`。无需启动新的服务。

## 评分口径

| 指标 | 含义 |
| --- | --- |
| DocumentHit@K | 前 K 个分块至少来自一篇已知目标论文 |
| TargetDocumentRecall@K | 命中的已知目标论文数 / 已标注目标论文数 |
| EvidenceRecall@K | 已完整覆盖的必需证据组数 / 必需证据组数，再对题目宏平均 |
| AllEvidence@K | 该题的必需证据组是否全部齐全，再对题目平均 |
| TargetRR@K | 首次完整覆盖任意一个已标注证据组所在排名的倒数，再对题目平均 |
| MRR / nDCG | 本批返回空值，尚无经审核的完整相关性判断池 |

TargetRR 衡量已标注目标证据的首次出现，不等同于普通 MRR；未标注分块不应被武断判为不相关。
同一论文的其他重叠分块若完整包含同一原文，也可覆盖该证据。只命中文档标题不能覆盖正文证据。
重复分块占据原始排名、不给额外覆盖；未知分块 ID、错误版本、遗漏问题、失败请求会阻止评分。

## 运行方式

在仓库根目录运行。所有产物写到测评目录，程序拒绝向冻结基线目录输出。

```powershell
# 校验草稿；去掉 --allow-draft 将要求全部问题经过人工审核
.venv\Scripts\python.exe scripts/pilot_benchmark.py validate --allow-draft

# 重新生成审核页
.venv\Scripts\python.exe scripts/pilot_benchmark.py review

# 完全本地 BM25
.venv\Scripts\python.exe scripts/pilot_benchmark.py run --strategy bm25 --allow-draft --output data/paper_benchmark/papers48-pilot20-v1/runs/bm25.json

# 已生成查询向量，可直接复用，后续运行不再调用 API
.venv\Scripts\python.exe scripts/pilot_benchmark.py run --strategy dense --query-vectors data/paper_benchmark/papers48-pilot20-v1/query-vectors.json --allow-draft --output data/paper_benchmark/papers48-pilot20-v1/runs/dense.json
.venv\Scripts\python.exe scripts/pilot_benchmark.py run --strategy hybrid --query-vectors data/paper_benchmark/papers48-pilot20-v1/query-vectors.json --allow-draft --output data/paper_benchmark/papers48-pilot20-v1/runs/hybrid.json

# 对保存的结果重放评分，不发出检索或模型请求
.venv\Scripts\python.exe scripts/pilot_benchmark.py score --run data/paper_benchmark/papers48-pilot20-v1/runs/hybrid.json --allow-draft --output data/paper_benchmark/papers48-pilot20-v1/runs/hybrid.replay.scores.json

# 将浏览器下载的审核记录导入新版本；把 reviews.json 替换为实际文件路径
.venv\Scripts\python.exe scripts/pilot_benchmark.py import-reviews --reviews reviews.json --output data/paper_benchmark/papers48-pilot20-v1/dataset.reviewed.json
.venv\Scripts\python.exe scripts/pilot_benchmark.py --dataset data/paper_benchmark/papers48-pilot20-v1/dataset.reviewed.json validate
```

若修改查询，需要重新生成查询向量。`embed-queries --output <新文件>` 会发送 20 条问题至配置的 DashScope、消耗额度，并拒绝覆盖已有缓存。2026-09-16 本次 20 条查询已获得用户明确授权并完成；后续应按新的问题内容及授权范围执行。

运行文件绑定基线清单哈希、数据集哈希、查询文本、代码哈希、策略配置和查询向量哈希。导入审核后数据集版本改变；旧运行不能直接冒充新版本运行。可重用未改变查询所对应的向量，重新执行本地检索。

## 已执行的开发诊断

**以下均为未审核的开发样本成绩，不用于简历中的正式质量结论。**

| 策略 | DocumentHit@10 | EvidenceRecall@10 | AllEvidence@10 | TargetRR@10 |
| --- | ---: | ---: | ---: | ---: |
| BM25 | 30.0% | 30.0% | 30.0% | 0.2417 |
| Dense | 100.0% | 82.5% | 80.0% | 0.5030 |
| BM25 + Dense / RRF | 100.0% | 87.5% | 85.0% | 0.5821 |

BM25 在这批 6 条英文题上全部命中已标注证据，在 14 条中文题上均未命中。语料主要为英文、查询处理未翻译；该结果混合了语言和题型因素，不能据此概括 BM25 的普遍性能，也不能作为 RRF 或重排收益的因果结论。

RRF 尚未找齐证据的题为 P20-12、P20-14、P20-20。其中 P20-20 只命中两篇目标论文中的一篇；P20-12 与 P20-14 命中了目标论文，但未覆盖当前标注的完整证据组。人工复核时也应检查返回结果是否包含尚未标注的等效依据，不能把这些候选片段都直接定为错误。

实现复用项目 QueryProcessor、BM25 打分器和 RRF（k=60），每路候选深度 20、输出深度 10。Dense 使用冻结的 993 个向量做精确余弦检索，**不是线上 Chroma ANN 或 MCP 全链路成绩**。重排关闭。不同策略按相同题目和 Top K 比较，未按相等上下文 token 预算比较；时间仅包含本地检索，不含载入、查询向量 API 或评分。

## Material Passport

- Material type: frozen-corpus retrieval pilot / implementation verification.
- Origin: local original PDFs and frozen MinerU exports; assistant-authored candidates, not user logs.
- Verification status: deterministic scoring replay VERIFIED; software checks passed; semantic human review PENDING.
- Execution: 60/60 actual retrieval requests across three strategies; all case-level scores and aggregate metrics exactly reproduced from saved runs.
- External processing: 20 queries only to DashScope text-embedding-v3, 1,024 dimensions; 2 successful API requests, 730 reported tokens. Three initial transport failures and successful calls are preserved in `query-embedding-usage.json`.
- Software evidence: 15 targeted tests passed; new-file Ruff checks passed; review JavaScript syntax check passed. Browser preview was policy-blocked, so visual and interaction validation are not claimed.
- Corpus evidence: `scripts/import_paper_baseline.py verify` rechecked all 548 artifact hashes, 48 documents, 993 chunks. The frozen snapshot was not modified.
- Limits: convenience sample of 20 development questions; no independent evaluation, no significance claim, no comprehensive relevance labels. Original 120-question formal benchmark gates remain separate.

下一步先审核这 20 题，尤其核对部分 PDF 匹配及跨论文证据，再补充各策略候选池的相关性标注。审核期间保留这次结果，不依据命中结果偷偷改题。

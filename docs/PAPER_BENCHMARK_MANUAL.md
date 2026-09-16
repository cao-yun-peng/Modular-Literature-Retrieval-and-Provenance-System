# 论文证据测评：构建、人工审核、评分与回归

这套工具面向项目自身的论文证据服务。评分不调用生成模型；DeepSeek 只辅助构建候选题。
原始论文不修改，副本、索引、模型调用记录和报告保存在 `data/paper_benchmark/v1`。
该目录被项目现有 `.gitignore` 排除；应作为本地评测数据单独备份，不把整篇论文提交到代码仓库。

## 已有数据与人工审核入口

- `corpus.json`：48 份去重 PDF、主题、原始路径、论文组、24/24 来源划分。
- `pages/`：PDF 物理页码，从 1 开始；规范化文本、字符偏移及文本哈希。
- `pilot_v2/review.html`：20 题试点，原文证据可点击回到 PDF。
- `authoring/candidates.json`：179 道结构有效的 DeepSeek 候选题。
- `draft_120.json`、`review_120.html`：按预定槽位选取的 120 题草稿（84 英文、36 中文）。
- `authoring/generation_summary.json`：180 次逻辑请求的输出状态及 token 用量；复用了 20 题缓存。

**所有生成的审核状态仍为 pending。结构/原文校验通过不等于人工金标，也不证明问题无歧义。**
一个候选没有提供支撑证据，被拒绝；相同槽位的备选进入 120 题草稿。
试点第一轮还发现双栏文本交错和模型改写原文的问题，修正后的 `pilot_v2` 才是复核入口；旧调用保留追踪。

浏览器打开复核 HTML，逐题阅读原 PDF、检查问题、答案要点与证据，填写审核人并导出 JSON。
需要调整证据组、原文位置或语言时编辑 JSON，再运行 validate。第二轮用 `review --blind` 导出，页面不显示首次审核结论；记录真实审核人，不宣称单人复核是独立标注。

## 命令

在项目根目录的 PowerShell 运行。下面省略长路径，使用项目虚拟环境。

```powershell
# 新建版本（原文件只读，按 PDF 文件内容识别缺失后缀）
.\.venv\Scripts\python.exe scripts/benchmark.py --root data/paper_benchmark/v2 prepare --source "E:\project\RAG_TEACHER\thesis-writing\参考文献-论文"

# 独立索引，成功文献可恢复；保留 nomic-embed-text，不修改生产 settings.yaml
.\.venv\Scripts\python.exe scripts/benchmark.py ingest

# 生成草稿。DEEPSEEK_API_KEY 可在项目 .env 配置，密钥不会写入报告
.\.venv\Scripts\python.exe scripts/benchmark.py generate --pilot
.\.venv\Scripts\python.exe scripts/benchmark.py generate

# 合并候选为每个预定槽位一题；仍是草稿
.\.venv\Scripts\python.exe scripts/benchmark.py assemble --dataset data/paper_benchmark/v1/authoring/candidates.json
.\.venv\Scripts\python.exe scripts/benchmark.py review --dataset data/paper_benchmark/v1/draft_120.json --output data/paper_benchmark/v1/review_120.html
.\.venv\Scripts\python.exe scripts/benchmark.py validate --dataset data/paper_benchmark/v1/draft_120.json

# 相同语料的三路候选池，人工填写 grade=0/1/2、reviewer、reviewed_at
.\.venv\Scripts\python.exe scripts/benchmark.py pool --dataset data/paper_benchmark/v1/draft_120.json
.\.venv\Scripts\python.exe scripts/benchmark.py review-judgments --dataset data/paper_benchmark/v1/draft_120.json --judgments data/paper_benchmark/v1/judgments-dev.json --output data/paper_benchmark/v1/review_judgments.html

# 来源证据与当前块的映射建议，不会自动将匹配结果标为人工相关
.\.venv\Scripts\python.exe scripts/benchmark.py map --dataset data/paper_benchmark/v1/draft_120.json
.\.venv\Scripts\python.exe scripts/benchmark.py diagnose --dataset data/paper_benchmark/v1/draft_120.json

# 检索 / 完整服务；草稿报告返回 1，保留结果但不能用于正式成绩
.\.venv\Scripts\python.exe scripts/benchmark.py run --dataset data/paper_benchmark/v1/draft_120.json --strategies bm25 dense hybrid
.\.venv\Scripts\python.exe scripts/benchmark.py run --dataset data/paper_benchmark/v1/draft_120.json --strategies service --repeats 3

# 同一候选池排序消融；LLM 三次，K=5/10 评分共享每次的实际排序
.\.venv\Scripts\python.exe scripts/benchmark.py run --dataset data/paper_benchmark/v1/draft_120.json --strategies rrf_pool cross_encoder llm --repeats 3

# 同一候选池、同一层级索引上的上下文扩展
.\.venv\Scripts\python.exe scripts/benchmark.py run --dataset data/paper_benchmark/v1/draft_120.json --strategies expand_none expand_neighbors expand_parent
```

旧入口兼容：`scripts/evaluate.py --benchmark-root data/paper_benchmark/v1 --test-set ... --level service --ks 5 10`。
不传 `--benchmark-root` 时原有 GoldenTestCase / CustomEvaluator 行为保持不变。

`replay --run <run目录> --output <新的summary.json>` 从保存响应离线重算，不调用模型；可指定同索引、同题目版本的新 `--judgments`。
使用 `replay --run <原run> --dataset <审核后标签> --judgments <新版相关性标签> --evidence-map <复核后映射> --as-run --output <新的run目录>` 会保存新的 manifest、逐题响应、分数和报告，可继续交给 compare/select。只更新金标或审核记录时不重发查询；问题文本、查询范围、参数发生变化则拒绝离线复用。只有题目数量、人工审核、标注完整度和重复次数均达标时，重算目录才可能升级为 complete；原目录保持不变。
`compare --baseline <run目录> --candidate <run目录> --strategies hybrid hybrid --output <comparison.json>` 生成配对差值、按论文组聚类的 bootstrap 区间和回归门槛。

退出码：0＝操作成功或正式运行完成；1＝质量未正式通过（草稿、漏标、异常或门槛不满足）；2＝配置、文件或版本契约错误。先看 manifest.status，不能把退出码 1 一概解释为算法质量低。

## 人工审核与冻结

1. 审核 `corpus.json` 中的论文组与 DOI。元数据抽取可能误认，不能仅凭自动分组确认无泄漏。必要时用 `prepare --family-overrides` 为新的版本提供 `{paper_sha256: family_label}`；相同正文/补充或版本应使用同一标签。重新划分后重新出题，不能沿用跨集合的旧标签。
2. 确认分组后，将 `family_review` 写为 `{"status":"approved","reviewer":"真实审核人","reviewed_at":"ISO时间"}`。
3. 逐题核验，`review` 保存相同三项及 notes；24 题还需 `second_review`。用 `review --blind` 导出盲复核页面，完成后记录 `blind: true`；不能仅为通过校验而填写未实际完成的审核。
4. 库外/错误前提题必须检查全语料：模型只能确认所选片段没有答案，不能自行证明整个库无答案。
5. `freeze --dataset <已审核JSON> --version 1.0 --output <golden-1.0.json>` 只选择 approved 题，检查 120 题、每类配额、60/60 划分、每部分 42/18 语言、来源位置、近重复以及审核记录；已有冻结文件不可覆盖。
6. 冻结后重新建立候选标注（标签以 dataset/index 哈希绑定），完成开发集对照。`pool --split test --dataset <冻结集>` 可以为测试题建立独立人工标注池，不输出测试成绩。测试题不参与调参；两部分的 judgments.cases 合并到同版本标签文件后再执行正式评分。
7. `select --dev-run <正式开发run> --dataset <冻结集> --strategies <基线 最终方案> --output <selection.json>` 预先声明最终方案。测试运行必须传 `--split test --selection ...`，不得绕过冻结和预声明检查。

每条金标的 `evidence_groups` 是必须共同覆盖的证据组；每组 `alternatives` 是可替代的方案，每个方案由一个或多个原文 span 构成。例如跨论文题有两组，命中其中一组只得 0.5 Evidence Recall。跨块片段必须覆盖全部非空白字符，不能省略数字或数学符号。

题目版本改变会使旧 judgments 失效，这是防止“问题已改、相关性标签仍沿用”的保护。先保留原 run，再补标、升级版本、重算所有方案。

精确文本匹配可能漏掉解析格式变化。`evidence_map.json` 中的自动 exact/partial 候选都保留 pending；人工对照 PDF 后，可填写某个 span 的 `review_status: approved`、`reviewer`、`reviewed_at`，以及 `approved_alternatives`。每个替代方案是若干 `{chunk_id, start, end}`，偏移以索引块原始 text 为准，每段至少 20 字符。一个跨块方案的所有片段都必须返回。

`run --evidence-map <已核验映射.json>` 或 `replay --evidence-map ...` 才使用这些复核结果。映射绑定题目及索引哈希，不修改原论文金标；未审核的模糊匹配不加分。映射后的片段也受 12,000 字符截断约束，不能仅凭 chunk_id 获得覆盖分。

已有三路检索 run 可用 `pool --from-run <run目录>` 直接导出候选，避免重新查询。标注页面中可搜索完整索引补充遗漏段落。映射/标签升级后需对所有方案使用同一版本离线重算。

## 指标与报告的解释

- **Evidence Recall@K**：覆盖的必需证据组数 / 总组数。**All-Evidence@K**：是否全部覆盖。
- **文献 Hit@K**：Top-K 证据中是否来自正确论文。找到论文不代表已经找到正确段落。
- **MRR@K**：首个 grade>0 结果的倒数排名，与 [BEIR 官方 MRR](https://github.com/beir-cellar/beir/blob/main/beir/retrieval/custom_metrics.py) 一致；另列 **Direct-MRR@K**，仅 grade=2（直接支撑）算正例。**nDCG@K**：0/1/2 线性增益和 log2 排名折损，参考 [BEIR 的 trec_eval 接口](https://github.com/beir-cellar/beir/blob/main/beir/retrieval/evaluation.py)；重复块只在第一次出现时获得增益。
- **Judged@K**：实际返回结果中的已审核比例。未标注不是不相关；未完成标注的 MRR/nDCG 为 null，报告 provisional。
- **证据保留率**：标准证据是否被当前全部索引块保留，区别于单次查询的覆盖率。
- **来源可追溯率**：论文身份可解析且返回文本在规范化原文中可验证。严格文本匹配失败需查看解析/转换差异，不能直接称为幻觉。
- **页码完整率**：有合法页码范围的返回比例。**页码正确率**：已声明页码中实际文本可在该范围验证的比例；全未知则为 N/A。**引用正确率**以全部返回证据作分母，缺页码得 0，防止通过不提供页码提高成绩。
- 原始证据、加入扩展、统一 12,000 字符预算三个成绩分别保留。预算依次消费每条原始证据及其扩展，未截断原始响应仍保存。
- 无答案题的 Recall/MRR 为 N/A。`coverage=not_evaluated` 只是功能未评估。手工 PDF 缺少 Zotero 身份，所以真实全文交接不在本版成绩中；协议测试验证不能伪造附件建议。
- 检索、重排、响应构造的 trace 与评分耗时分离。缺少 token usage 时保留 null，不记成 0；模型调用记录足以按实际账户价格换算成本。
- 同一语料/索引/题目/标注/预算才可直接比较。原 12 题历史成绩不能直接与新 120 题草稿比较。

每次 run 包含 `manifest.json`、输入标签快照、`query_results.jsonl`、`summary.json`、`report.md`。
分组成绩不混合 K、策略或重复轮次；按语言、题型、主题、来源划分及首个/后续请求另列。
当前 CLI 每次启动独立进程，报告区分 first_request 与 warm；模型服务器、操作系统缓存未清空，不将首个请求称为受控冷启动。需要受控冷启动时应在独立测试环境重启相关服务后单独运行并记录环境，不能把该延迟与预热样本混合。
共享论文的跨论文问题合并为 bootstrap 统计组；有效组数不足 2 时不报告置信区间。

当前独立入库配置明确使用论文 loader、层级切分和规则文本转换，关闭 LLM 改写；embedding 模型不变。
GROBID 不可用时的真实降级记录在入库日志中。批量 embedding 只用于独立入库，使用相同模型和 cosine 向量空间。
这些配置与生产默认配置存在差别，报告中必须保留，不能把独立索引成绩冒充原生产配置成绩。

## 验证

```powershell
.\.venv\Scripts\python.exe -m pytest tests/unit/test_paper_benchmark.py tests/unit/test_benchmark_mapping.py tests/unit/test_benchmark_runtime.py tests/integration/test_benchmark_stdio.py tests/unit/test_evaluate_cli.py tests/unit/test_eval_runner.py tests/unit/test_custom_evaluator.py tests/unit/test_llm_reranker.py tests/unit/test_pipeline_progress.py tests/unit/test_hybrid_search.py tests/unit/test_protocol_handler.py tests/unit/test_bm25_build_equivalence.py tests/unit/test_bm25_indexer_roundtrip.py -q
```

真实论文候选、人工金标、协议测试 fixture 和模型调用结果严格分开。测试通过只证明这些指定行为，不替代实际语料上的人工复核和正式测评。

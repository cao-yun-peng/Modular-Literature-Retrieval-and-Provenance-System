# 48 篇论文的固定语料与索引基线

本次任务记录在 `tasks/evidence_benchmark.md`。此文件说明基线产物与使用方式；实际状态以基线清单为准。

## 范围与状态

- 2026-09-16 用户要求将原 48 篇论文通过当前项目流程重新导入，然后固定为后续评测基线。
- 48 份去重原 PDF，580 物理页，163,527,855 字节；逐篇 SHA256 与原语料清单一致。
- 当前状态：48/48 篇已成功入库，并于北京时间 2026-09-16 12:58 冻结。993 个分块、993 条 1,024 维向量，最大分块 2,500 token。没有新的检索质量成绩。
- 使用独立 `papers48_baseline` 集合及独立工作台登记库；旧两篇工作台文献和 v1 评测索引保留。

## 固定流程

MinerU Agent → 原始 Markdown → 结构整理与摘要去重 → 2,500 token 上限、同章节 200 token 目标重叠 → DashScope `text-embedding-v3`、1,024 维 → Chroma + BM25。

`cl100k_base` 是项目明确使用的计数器。不是每块固定 2,500 token，也不是按每篇论文固定分块数。模型调用使用完整分块输入。

9 篇原件超过 MinerU 单次请求限制，使用无损页段副本；全部语料共 60 个解析输入。238 个分段页面的提取文字及 0.5 倍比例渲染像素均与原页一致，原件未改动。分段的页范围和哈希保存在 `plan.json` 与解析缓存中。合并后的 Markdown 仍只具备文档/章节定位，不将页段范围伪装成精确段落坐标。

## 产物

目录：`data/baselines/papers48-20260916/`。

- `plan.json`：48 篇原件、60 个解析输入、配置、源码哈希与发送目的地；凭据已脱敏。
- `checkpoint.json`：逐篇文献 ID、运行 ID、处理结果，支持中断后检查。
- 完成冻结后：`manifest.json`、`documents.json`、`chunks.json`、`vectors.json`、`bm25.json`、`settings.json`、登记库快照、48 份原件及逐篇运行产物。
- `manifest.json` 记录产物 SHA256、文献数、块数、向量维度、最大 token 数和冻结时间。`quality_evaluation` 保持 `not_yet_evaluated`。
- `source/` 保存实际入库版本的源码和配置；`recovery/` 保存第 41 篇上传连接超时及成功恢复的证据。该失败发生在写入索引前，原任务经查询确认为 `waiting-file` 后才重新提交。

## 实际验收

- 原件 48/48、580 页，哈希与输入语料一致。
- 工作台、Chroma、BM25 的分块 ID 集合相同，共 993 个；向量均为有限数值、1,024 维。
- 548 个快照文件哈希检查通过。
- 全部 550 个快照文件已设为只读；设置后再次通过哈希校验。550 包含清单本身和运行检查点，548 是清单中逐项列出的受检产物数。
- 使用已存向量逐篇读回测试，48/48 篇的代表性分块可检索；没有调用外部模型，不将此检查计为检索质量成绩。
- 实际 HTTP 接口返回 48 篇已成功文献；PDF Range 阅读与分块读取成功；冻结后新增上传和重新摄入均返回 `409 baseline_frozen`。
- 原工作台的两篇文献、45 个分块仍保留在旧登记库。
- 68 项相关测试通过；集合隔离后再次运行的 21 项工作台测试通过；前端生产构建通过。

## 运行与校验

```powershell
.\.venv\Scripts\python.exe scripts/start_workbench.py
.\.venv\Scripts\python.exe scripts/import_paper_baseline.py prepare
.\.venv\Scripts\python.exe scripts/import_paper_baseline.py import
.\.venv\Scripts\python.exe scripts/import_paper_baseline.py freeze
.\.venv\Scripts\python.exe scripts/import_paper_baseline.py verify
```

`prepare` 只做本地清单和页段检查。`import` 通过工作台将 PDF 发往 MinerU、文本发往 DashScope；本次完整范围已获得用户明确授权。`freeze` 只在全部成功、没有活动摄入任务、原件/分块/双索引一致时创建快照与写入保护；`verify` 检查快照文件哈希。

上述前四条命令记录本次构建流程。冻结完成后使用 `verify` 检查，勿重新执行 `prepare/import/freeze`。未冻结的批次若发生失败，应先核实外部任务状态，再运行 `scripts/resume_paper_baseline.py`；它保留失败记录，通过工作台重试接口恢复，跳过已成功文献。

冻结后继续在此集合上检索、标注和评测；如需修改分块、模型或原件，应新建独立集合和新版本，保留本基线。旧 v1 分块 ID 和检索成绩不能直接移用于本集合。

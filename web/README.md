# 论文 RAG 工作台

本机个人工作台。React、TypeScript、Vite、Tailwind、shadcn/ui 风格基础组件、TanStack Router/Query/Table、PDF.js、Recharts；Python FastAPI 接口调用现有摄入与检索服务。依赖锁定在 `pnpm-lock.yaml` 和仓库根目录 `uv.lock`。

## 安装和启动

在仓库根目录，使用 Node 24、pnpm 11 和 uv：

```powershell
uv sync --frozen --extra web --extra dev
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
.venv/Scripts/python.exe scripts/start_workbench.py
```

打开 http://127.0.0.1:8765/。默认仅监听本机。开发时另开终端运行 `pnpm --dir web dev`，Vite 将 `/api` 转发到 8765。启动脚本可传 `--port`，变更端口时同步调整开发代理。

模型凭据继续从现有 `.env` / `.env.local` 与 `config/settings.yaml` 读取。浏览器只收到已配置标记，不收到 API key。此版本使用配置中的一个知识库，不支持页面切换模型。

## 工作流程

1. 文献库添加 PDF（项目支持 50 MB、200 页），保存后点击开始处理。超过 MinerU 单次 10 MB/20 页的原件会生成无损页段副本，按原顺序解析合并，项目内仍保留完整原件和单篇文献身份。同内容重复上传返回原文献；相同配置与入库版本的成功结果直接打开。
2. 论文页对照 PDF、原始 Markdown、整理后 Markdown 和分块；URL 保存运行、分块和阅读模式。PDF 支持翻页和缩放，未提供页内坐标时不显示高亮映射。
3. 运行记录显示真实事件、阶段耗时、缓存与存储结果；导入历史缺失的耗时显示未记录。
4. 检索实验室限定文献、选择 3/5/10 条证据，运行仅检索或检索并回答。证据编号链接到完整分块；分数保留原始方法与数值。
5. 研究 Agent 页面（`/research`）进行本地多轮研究，支持综述、发展时间线和问答。实时查看查询、证据、覆盖缺口和停止原因，点击报告 `[E1]` 引用查看原文，下载 Markdown 与研究记录。历史可刷新恢复；失败或中断可完整重试。研究结果区分草稿、部分结果和证据不足。网页暂不开放联网下载或 Zotero 写入。详细接口与边界见 [研究 Agent 说明](../docs/RESEARCH_AGENT.md#网页研究工作台)。

固定处理方案：MinerU Agent → 结构整理与摘要去重 → `cl100k_base` 计数、2500 tokens 上限、同章节 200 tokens 目标重叠 → 阿里云 `text-embedding-v3` 1024 维完整输入 → Chroma + BM25。短块不视为异常。

2026-09-16 起默认集合为 `papers48_baseline`：48 篇论文、993 个分块，已冻结并禁止上传或重新处理。旧测试集合和旧验证数据已列入清理清单，物理删除待确认；上面的上传步骤适用于另建的可写集合。当前工作台目录为 `data/web-collections/papers48_baseline-8a73dc3c/`，Chroma 为 `data/chroma-papers48-baseline/`，BM25 为 `data/bm25/papers48_baseline/`。

基线导入与校验使用 `scripts/import_paper_baseline.py`；只有 `data/baselines/papers48-20260916/manifest.json` 存在且状态为 `frozen`，才表示实际入库与一致性校验完成。冻结后的项目摄入入口拒绝向该集合添加或重新处理文献；检索和证据阅读继续可用。详细记录见 [48 篇基线说明](../docs/PAPERS48_BASELINE.md)。

## 持久化与恢复

- 当前工作台目录下的 `workbench.sqlite3` 登记文献、任务、事件、分块和集合恢复状态；原 PDF 在 `uploads/<hash>/source.pdf`。
- 同目录的 `runs/<run_id>/` 保存运行产物，`embedding-cache/` 保留已成功完成的完整向量批次。解析缓存位于 `data/mineru-agent/`，包含原件和无损页段两类缓存。
- 单进程 worker 所有权由文件锁保护：摄入串行、检索任务最多两个、研究任务最多一个。SSE 支持 Last-Event-ID，页面关闭不取消任务。
- 服务重启将未完成任务标记中断，用户显式重试；外部收费调用不承诺恰好一次。
- 双索引写入时阻断该集合的新查询；部分失败标记需要恢复。重试同一文献，复用已保存向量并重新写入双索引，只有两者完成才恢复查询。
- 回答失败仍可阅读证据，重试回答复用证据。
- 原 Streamlit 在迁移期间保留。不要同时用另一个进程对相同集合写入；SQLite 任务保护作用于本工作台的请求。

备份应包含当前工作台目录、配置对应的 Chroma/BM25 目录、MinerU 缓存、`data/baselines/papers48-20260916/` 和 `data/paper_benchmark/papers48-pilot20-v1/`。不要在任务执行中复制数据库文件作为一致性备份。

## 当前基线与测评校验

以下命令完全本地运行，不重新解析或向量化，不改变基线：

```powershell
.venv/Scripts/python.exe scripts/import_paper_baseline.py verify
.venv/Scripts/python.exe scripts/pilot_benchmark.py validate --allow-draft
```

20 题仍待人工审核，已有 BM25/Dense/RRF 试跑仅作为开发诊断。审核与离线重放方法见 [测评说明](../docs/PAPERS48_PILOT20.md)。

## 接口契约与验证

OpenAPI 地址 http://127.0.0.1:8765/docs，前缀 `/api/v1`。创建任务需要 `Idempotency-Key`。分页返回 `items` 和 `next_cursor`；统一错误包含 `code`、`message`、`retryable`、`request_id`。

修改 DTO 后同步前端类型：

```powershell
.venv/Scripts/python.exe scripts/start_workbench.py --export-openapi output/web-openapi.json
pnpm --dir web api:types
pnpm --dir web build
```

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_web_api.py tests/unit/test_structured_tokens.py tests/unit/test_mineru_agent.py tests/unit/test_dashscope_paper_ingestion.py tests/unit/test_document_chunker_paper.py -q -o cache_dir=output/pytest-cache
pnpm --dir web test
pnpm --dir web exec playwright install chromium
pnpm --dir web test:e2e
```

浏览器端到端测试要求 8765 服务运行且存在成功导入的文献，覆盖 PDF 阅读、URL 恢复、窄屏主题和无效上传。旧单篇论文真实 API 验证脚本和结果目录已列入清理清单，当前入口不再使用。当前基线完整性检查使用上面的 `verify`，不触发收费调用。

布局参考与许可证见 [THIRD_PARTY.md](THIRD_PARTY.md)。HTML 经白名单净化后展示，远程图片以占位文字显示；PDF 原图仍可在原文中阅读。

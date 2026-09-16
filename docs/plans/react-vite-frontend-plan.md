# React + Vite 前端重构计划

日期：2026-09-15。状态：选型与实施计划，尚未开始重构。本文将用户的“vist”理解为 Vite。首期面向本机单用户论文 RAG 工作台。

## 1. 选型结论

采用 React + TypeScript + Vite 构建 SPA，FastAPI 暴露 HTTP API，复用现有 Python 摄入、分块、Embedding、Chroma、BM25 与 MCP 检索服务。当前项目没有独立 Web HTTP API；Streamlit 直接调用 Python 服务，MCP 的 stdio 通道不能直接作为浏览器接口。

主要视觉参考选定 [satnaing/shadcn-admin](https://github.com/satnaing/shadcn-admin)，[在线示例](https://shadcn-admin.netlify.app/)。其仓库和 package.json 确认使用 React、Vite、TypeScript、Tailwind、TanStack Router/Query/Table，具有侧栏、明暗主题与响应式布局。参考布局与组件组织，按论文业务重新编排；不整库引入其示例业务、Clerk 登录或模拟数据。其 README 也说明它不是开箱即用的 starter，应按需选取。

[MIT LICENSE](https://github.com/satnaing/shadcn-admin/blob/main/LICENSE)：复制实质代码时保留版权和许可文件，并记录参考提交。

比较：[RAGFlow](https://github.com/infiniflow/ragflow)与业务更接近，但属于完整 RAG 产品，整套迁入会与已有后端重叠；[Dify](https://github.com/langgenius/dify)面向更广的应用与工作流，许可证包含额外条件。最终选择 shadcn-admin 作为单一主要 UI 参考。

| 层 | 选型 | 本项目用途 |
|---|---|---|
| 前端核心 | React + TypeScript + Vite | 页面、严格类型、开发与构建 |
| UI | Tailwind CSS + shadcn/ui + Lucide | 布局、表格、侧栏、标签页、抽屉 |
| 路由 | TanStack Router | 文献、运行记录、分块详情的可分享 URL |
| 服务端数据 | TanStack Query + fetch | 缓存、分页、请求状态；浏览操作不触发运行 |
| 表格/图表 | TanStack Table + Recharts | 文献/分块筛选，token 分布、阶段耗时 |
| 表单 | React Hook Form + Zod | 上传与查询参数校验 |
| 文档阅读 | PDF.js；react-markdown + remark-gfm + remark-math + rehype-katex | 原 PDF、Markdown、公式展示 |
| HTTP 服务 | FastAPI + Pydantic + Uvicorn | 类型化 REST API 与 OpenAPI |
| 任务进度 | SSE + SQLite 任务/事件记录 + 单写入 worker | 长任务不占用 HTTP 请求，刷新后恢复状态 |
| 测试 | pytest + Vitest + Testing Library + Playwright | 接口契约、关键交互与端到端流程 |
| 包管理 | pnpm；现有 Python uv 工作流 | 各自锁文件固定依赖 |

不为首期新增 Redux、可视化 DAG 编辑器、Redis、Celery、用户系统。固定流程用步骤条和详情面板呈现。执行时基于所选参考提交核验 Node 与依赖兼容性，提交精确 lockfile；不在计划阶段盲目锁定最新补丁号。

## 2. 保持的业务规则

MinerU Agent → 结构整理与明确摘要去重 → 2500 tokens 硬上限、200 tokens 目标重叠 → 阿里云 text-embedding-v3 / 1024 维 → Chroma + BM25。

默认 tokenizer 为 cl100k_base，界面标明工程计数口径。保留实际重叠量、分块类型、章节、figure_id、linked_figures、chunking_strategy、来源与模型版本。参数第一版只读，不能由浏览器任意覆盖已统一的方案。

保留原 PDF、MinerU 缓存与已有新旧向量库。当前轻量解析没有坐标：支持人工浏览 PDF 和定位到章节/分块，不能承诺精确页内高亮。查询排名分数按原始分数和方法展示，不能把 RRF 或 rerank 分数伪装成相关概率百分比。

## 3. 页面与样式

建议产品名称：论文 RAG 工作台。整体参考 shadcn-admin 的应用框架，自定义论文工作区。

- 背景 #F8FAFC、面板白色、正文 #0F172A、边框 #E2E8F0；主色 #2563EB。成功/处理中/失败分别用绿/蓝/红，并同时显示文字与图标。
- 默认浅色，提供深色；中文系统字体，正文阅读区独立字号与行距；8px 间距体系、8–12px 圆角，弱阴影。
- 左侧约 224px 可折叠导航；顶部显示面包屑、当前知识库和运行状态。主要页面使用信息密度适中的表格；正文阅读区可调整宽度。
- 桌面双栏，窄屏改为标签页；保证键盘操作、焦点可见、加载/空/失败状态。

| 页面 | 布局及主要交互 |
|---|---|
| 文献库 | 文件名、解析状态、分块数、模型、入库时间；搜索、集合筛选、进入文献详情；二期加入上传 |
| 论文工作台 | 顶部真实运行步骤；左栏 PDF/原始 Markdown/整理结果；右栏分块列表与正文，展示 tokens、重叠、类型、来源；选块查看关联图注 |
| 运行记录 | 历史任务列表、成功/失败/中断状态、阶段耗时与事件；实际缺失的计时显示“未记录” |
| 检索实验室 | 输入问题、限定文献、top-k；左栏回答，右栏证据；点击引用进入分块，展开图注；显示召回/重排分数的真实语义 |
| 系统状态 | MinerU、Embedding 模型、维度、2500/200、当前 collection 与可用状态；凭据仅显示是否配置 |

MVP 导航先开放文献库、论文工作台、检索实验室；运行记录/系统状态随后补齐。工程元数据放入折叠详情，不占据默认阅读区。

## 4. 接口契约（计划新增，当前尚不存在）

统一前缀 /api/v1。成功响应为 JSON；列表采用 {items, next_cursor}，错误采用 {error:{code,message,retryable}, request_id}，配合正确 HTTP 状态码。Python 模型生成 OpenAPI，并从 OpenAPI 生成前端类型。

| 方法/路径 | 输入和输出 |
|---|---|
| GET /health | 应用状态；外部模型探活不自动计费调用 |
| GET /config/public | 脱敏的 parser、embedding、chunking、collection 配置 |
| GET /collections | 可用集合及文献/分块统计 |
| GET /documents?collection=&cursor=&limit=&q= | 分页文献摘要 |
| GET /documents/{document_id} | 元数据、当前成功运行、可用原文/解析资源 |
| GET /documents/{document_id}/source | 已登记 PDF 的受控文件响应，支持 Range |
| GET /runs/{run_id}/artifacts/{kind} | raw_markdown、normalized_markdown、structure 等白名单资源 |
| GET /runs/{run_id}/chunks?cursor=&limit=&type=&section= | 该运行的分块列表/摘要，避免一次加载全文 |
| GET /chunks/{chunk_id}?run_id= | 分块全文、计数、实际重叠、关联资产、来源信息 |
| POST /documents/upload | multipart PDF；验证限制，存储后返回 document_id，不自动消费模型 API |
| POST /ingestion-runs | {document_id,collection} + Idempotency-Key；202 返回 run_id |
| GET /runs?document_id=&cursor= | 历史运行列表 |
| GET /runs/{run_id} | 当前状态、阶段、结果/错误、配置快照 |
| GET /runs/{run_id}/events | SSE，支持 Last-Event-ID 续读 |
| POST /retrieval-runs | {query,collection,document_ids,top_k}；202 返回 run_id |
| GET /retrieval-runs/{run_id}/result | 结构化 evidence、citations、linked_assets、scores；可选 answer |

上传与运行分离；历史结果默认只读。首次上传页说明论文发送 MinerU、分块发送已配置阿里云 API；模型密钥留在 Python 后端，前端 VITE_* 变量中不得放密钥。

对象标识统一：document_id 为稳定文档 ID，content_hash 为原文件哈希，run_id 标识一次运行，chunk_id 标识分块。collection 与运行配置快照明确保存；不混用当前代码中的 doc_id 全哈希和 doc_前缀 ID。

SSE 事件包含 event_id、run_id、stage、status、timestamp、completed_units/total_units（有实测值才填写）、message。阶段采用 queued/integrity/load/structure/split/embed/upsert/completed/failed/interrupted。任务分阶段进度不伪装为按耗时估算的精确百分比；当前 on_progress 只覆盖粗阶段，结构整理和 Embedding 批次数需新增观测事件。

任务状态存 SQLite，单 worker 串行摄入/写索引，查询受控并发；不能把长任务完全托付给请求生命周期或仅内存后台任务。重启后的未完成任务标为 interrupted，使用缓存恢复可恢复阶段；不盲目自动重新调用收费 API。第一版不承诺任意阶段取消与回滚。

本地开发 Vite 代理 /api 到 FastAPI；交付时同源提供构建产物与 API。默认只监听 loopback，文件接口仅按已登记 ID 取文件，不接收任意本机路径。Markdown 禁止执行原始 HTML，表格单独做白名单净化。避免向前端暴露 API keys、签名上传 URL 和无必要的本机绝对路径。

## 5. 现有模块如何复用

- IngestionPipeline.run：继续负责摄入，不在 React 重写分块或 Embedding。
- DocumentManager / ChromaStore / BM25Indexer：提供文献与块查询，统一使用配置中的 collection，修正 DataService 当前的 default 固定回退。
- QueryKnowledgeHubTool.execute：复用检索能力，增加结构化 DTO 适配；不能让前端解析 Markdown 字符串来重建 evidence。
- TraceContext / TraceCollector / TraceService：复用已有观测机制，补齐 API 发起任务的统一 collect/finish 与失败状态。
- output/mineru-qwen2500：作为第一篇历史验证快照导入，通过通用运行记录读取；保留 source=artifact_import 标记，缺少的耗时不编造。新页面不硬编码单一输出目录。
- 现有 Streamlit 的 _run_ingestion 未检查 pipeline.run 返回的 success，可能将失败显示为成功；新 API 以返回结果和任务状态为准，纳入测试。
- MCP 继续使用原 stdio 入口，与 FastAPI 共享业务服务，不在浏览器存放或运行 MCP stdio 客户端。

建议新增 web/src/{app,components,features,lib}，其中 features 为 documents、paper-workbench、runs、retrieval、settings；Python 新增 src/web_api/{app,routers,schemas,services}。已有业务模块逐步抽取公共服务，避免 API 依赖 Streamlit 控件。

## 6. 实施顺序与验收

| 阶段 | 交付 | 验收 |
|---|---|---|
| P0 契约与版式 | API DTO、任务状态、设计 token、页面结构 | 明确历史/实时、来源定位限制、集合和文档 ID 语义 |
| P1 只读工作台 | React 壳、FastAPI 读取服务、文献详情与分块浏览 | 真实展示本文 15 块，最大 2316 tokens，3 图注；原文和 token 与快照一致 |
| P2 实时摄入 | 上传、持久化任务、SSE 与进度日志 | 刷新/重连不丢任务；失败不显示成功；重复点击不重复提交；禁止部分向量误入库 |
| P3 检索实验室 | 检索、证据面板、引用跳转和回答 | 三个既有问题可复测，引用能返回真实块；模型调用失败有明确状态 |
| P4 整合与切换 | 运行记录、模型状态、生产构建、本地启动脚本 | 关键端到端流程通过、无凭据进入前端；React 覆盖原界面后才移除旧展示代码 |

第一可演示版本以 P1 为目标。P1 无需重新调用 API；P2/P3 才执行用户触发的真实任务。正式迁移前始终保留可用 Streamlit 入口和已有索引。

测试聚焦：API 分页/ID/资源访问、错误码、任务幂等、SSE 续读、失败状态、2500 硬上限、输入不截断、引用跳转；浏览器验证上传到检索闭环。使用本篇论文作为回归样本，同时用合成长章节覆盖 200 tokens 目标重叠场景。只读导入与实时结果分开验收。

## 7. 参考资料

- [参考项目及其实际依赖](https://github.com/satnaing/shadcn-admin/blob/main/package.json)
- [shadcn/ui 安装：支持 Vite](https://ui.shadcn.com/docs/installation)
- [Vite 官方指南](https://vite.dev/guide/)
- [TanStack Query：服务端数据缓存与同步](https://tanstack.com/query/latest/docs/framework/react/overview)
- [FastAPI：OpenAPI 与数据校验](https://fastapi.tiangolo.com/features/)
- [PDF.js](https://mozilla.github.io/pdf.js/)

# Modular RAG MCP Server

<div align="center">

**一个模块化、可插拔、面向学术论文证据检索的 RAG MCP Server**

[![Python](https://img.shields.io/badge/Python-3.10%2B-blue)](https://www.python.org/)
[![MCP](https://img.shields.io/badge/MCP-1.0%2B-green)](https://modelcontextprotocol.io/)
[![License](https://img.shields.io/badge/License-MIT-yellow)](LICENSE)
[![Tests](https://img.shields.io/badge/Tests-pytest-blue)](tests/)

</div>

---

## 目录

- [项目概述](#项目概述)
- [核心特性](#核心特性)
- [架构总览](#架构总览)
- [快速开始](#快速开始)
- [安装](#安装)
- [配置](#配置)
- [使用指南](#使用指南)
  - [文档摄入](#文档摄入)
  - [Zotero 文献同步](#zotero-文献同步)
  - [知识检索](#知识检索)
  - [检索评测](#检索评测)
  - [MCP Server 模式](#mcp-server-模式)
  - [可视化 Dashboard](#可视化-dashboard)
  - [学术论文模式](#学术论文模式)
- [项目结构](#项目结构)
- [测试](#测试)
- [可扩展性](#可扩展性)
- [分支说明](#分支说明)
- [License](#license)

---

## 项目概述

**Modular RAG MCP Server** 是一个基于 **检索增强生成（RAG）** 与 **模型上下文协议（MCP）** 的文献证据检索服务。它可独立运行为文档检索引擎，也能以 MCP Server 的身份接入 GitHub Copilot、Claude Desktop 等 AI 工具，为上层 LLM 返回可回溯的论文证据、章节和引用定位。

对于已知论文的全文阅读，项目可返回 Zotero Item/Attachment Key 与 Handoff 建议；实际全文读取和引用插入仍由上层 Agent 调用 Zotero 工具完成，项目不会将该行为伪装为自身已完成的操作。

> **设计理念**：**教是最好的学（Learning by Teaching）**。本项目既是一份 RAG 技术的实战答案，也是一套配套的教学资源——每个模块的设计都对应着高频 RAG 面试考点。

---

## 核心特性

### RAG 检索策略

| 阶段 | 技术 | 说明 |
|---|---|---|
| **分块** | 论文感知 / Parent–Child 分块 | 保留章节、页码、图表和父子块关联；层级模式可按 feature flag 开启 |
| **粗排召回** | Hybrid Search (BM25 + Dense) + RRF 融合 | 关键词精确匹配 + 语义向量互补，平衡查全与查准 |
| **精排重排** | Cross-Encoder / LLM Rerank | 对候选集深度语义打分，两段式架构提升 Top-N 精准度 |
| **证据响应** | Evidence Bundle + 引用标注 | 返回可回溯的证据、章节/页码、分数及可选上下文，而非伪造全文答案 |

### 学术论文深度支持

- **GROBID 集成**：自动提取论文标题、作者、摘要、章节结构、图表标题
- **图表分块与连带召回**：图表独立成块，正文引用自动替换为占位符，检索时连带拉取图表
- **DOI / arXiv 识别**：自动提取论文标识符
- **优雅降级**：GROBID 不可用时自动回退到正则启发式提取
- **层级证据扩展**：命中 Child 后可按需补充 Parent 或相邻块，并在响应中保留原始证据定位

### Zotero 来源与 Agent Handoff

- **只读增量同步**：通过 Zotero Desktop Local API 读取指定收藏夹的 PDF 附件，以 SHA256 和 SQLite 状态表判断新增、更新与跳过
- **来源可回溯**：在检索结果中保留 Zotero Item Key、Attachment Key 与可用的 Citation Key
- **全文交接而非代理**：当证据不足或问题要求通读全文时，返回包含原因和附件标识的建议，由上层 Agent 调用 Zotero `fulltext`
- **安全默认值**：仅接受 loopback Local API；同步不写入 Zotero，历史附件仅标记 inactive，不会被自动物理删除

### 全链路可插拔架构

每一层定义抽象接口，配置文件一键切换，**零代码修改**即可更换后端：

| 组件 | 支持 |
|---|---|
| **LLM** | Azure OpenAI / OpenAI / DeepSeek / Ollama / Anthropic |
| **Embedding** | OpenAI / Azure / Ollama / 兼容 OpenAI 协议的任何服务 |
| **向量数据库** | ChromaDB / 可扩展到 Qdrant、Milvus |
| **重排序** | Cross-Encoder / LLM Rerank / None |
| **评估体系** | Hit@K / Recall@K / MRR / nDCG / RAGAS / Custom |

### MCP 生态集成

作为 MCP Server 运行，暴露标准 Tools 接口，直接接入 Copilot、Claude Desktop 等客户端——**零前端开发**即可拥有 AI 知识库。

### 可观测性

- 结构化日志（JSON Lines）
- 全链路 Trace（摄入 / 查询 / Zotero 同步 → 证据响应）
- 版本化评测产物（run ID、测试集 SHA256、逐查询结果）与质量门禁
- Streamlit Dashboard 六页面可视化监控

---

## 架构总览

```
┌────────────────────────────────────────────────────────────┐
│                     MCP Clients                             │
│          (GitHub Copilot / Claude Desktop / ...)            │
└─────────────────────┬──────────────────────────────────────┘
                      │ MCP Protocol (JSON-RPC over stdio)
┌─────────────────────▼──────────────────────────────────────┐
│                    MCP Server                               │
│  ┌──────────────┐ ┌──────────────┐ ┌───────────────────┐   │
│  │query_knowledge│ │   list_      │ │  get_document_    │   │
│  │    _hub       │ │ collections  │ │     summary       │   │
│  └──────┬───────┘ └──────────────┘ └───────────────────┘   │
│         │                                                   │
│  ┌──────▼──────────────────────────────────────────────┐   │
│  │                 Retrieval Pipeline                   │   │
│  │ QueryProcessor → HybridSearch → Reranker → Evidence  │   │
│  │ (BM25 + Dense + RRF Fusion + Cross-Encoder/LLM)      │   │
│  │ + Parent/Neighbor expansion + Handoff decision       │   │
│  └─────────────────────────────────────────────────────┘   │
└────────────────────────────────────────────────────────────┘

┌────────────────────────────────────────────────────────────┐
│       Manual PDF / Zotero Local API → Ingestion Pipeline    │
│                                                             │
│  PDF → Loader → Chunker → Transform → Encode → Store       │
│         │          │          │          │        │         │
│    [MarkItDown] [Recursive] [Refine]  [Dense]  [ChromaDB]  │
│    [GROBID]    [Paper-aware][Caption] [Sparse] [BM25]       │
│    [PyMuPDF]                                               │
└────────────────────────────────────────────────────────────┘
```

---

## 快速开始

### 前提条件

- Python 3.10+
- [Ollama](https://ollama.ai/)（用于本地 Embedding，可选但推荐）
- [GROBID](https://github.com/kermitt2/grobid)（用于论文深度解析，可选）

### 5 分钟体验

```bash
# 1. 克隆项目
git clone https://github.com/YOUR_USERNAME/modular-rag-mcp-server.git
cd modular-rag-mcp-server

# 2. 创建虚拟环境
python -m venv .venv
source .venv/bin/activate  # Windows: .venv\Scripts\activate

# 3. 安装依赖
pip install -e ".[dev]"

# 4. 编辑配置文件 config/settings.yaml
#    修改 LLM 和 Embedding 的 provider/api_key

# 5. 摄入一篇文档
python scripts/ingest.py --path papers/your_paper.pdf --collection my_docs

# 6. 查询
python scripts/query.py --query "你的问题" --collection my_docs
```

---

## 安装

### 基础安装

```bash
pip install -e .
```

### 安装开发依赖（测试、格式化、类型检查）

```bash
pip install -e ".[dev]"
```

### 可选依赖

```bash
# MarkItDown（PDF 解析核心）
pip install markitdown

# PyMuPDF（PDF 图片提取）
pip install PyMuPDF

# Ollama 客户端（本地 Embedding）
pip install ollama

# lxml（GROBID TEI XML 解析）
pip install lxml

# Streamlit（Dashboard）
pip install streamlit
```

### 启动 GROBID（学术论文模式）

```bash
# 使用 Docker（推荐）
docker run -d -p 8070:8070 lfoppiano/grobid:latest

# 或从源码运行：https://github.com/kermitt2/grobid
```

### 启动 Ollama（本地 Embedding）

```bash
# 安装 Ollama 后拉取 Embedding 模型
ollama pull nomic-embed-text

# （可选）拉取对话模型用于 Rerank
ollama pull deepseek-r1:1.5b
```

---

## 配置

所有配置集中在 `config/settings.yaml`：

```yaml
# LLM —— 用于 Rerank 和可选的元数据增强
llm:
  provider: "deepseek"     # openai / azure / ollama / deepseek
  model: "deepseek-chat"
  api_key: ${DEEPSEEK_API_KEY}

# Embedding —— 用于 Dense Retrieval
embedding:
  provider: "ollama"        # openai / azure / ollama
  model: "nomic-embed-text"
  base_url: "http://localhost:11434"

# 向量数据库
vector_store:
  provider: "chroma"
  persist_directory: "${MODULAR_RAG_DATA_DIR:-data}/chroma"

# 检索参数
retrieval:
  dense_top_k: 20
  sparse_top_k: 20
  fusion_top_k: 10       # RRF 融合后取 top-k
  rrf_k: 60               # RRF 常数

# 重排序
rerank:
  enabled: true
  provider: "llm"          # none / cross_encoder / llm
  top_k: 5

# 摄入参数
ingestion:
  chunk_size: 1000
  chunk_overlap: 200
  splitter: "recursive"
  batch_size: 100
```

Zotero 同步、层级分块与全文 Handoff 默认关闭；建议在新 collection（如 `papers-v2`）完成对照评测后再开启：

```yaml
sources:
  zotero:
    enabled: true
    base_url: "http://127.0.0.1:23119"
    read_only: true

ingestion:
  hierarchical_chunking:
    enabled: true

agent_handoff:
  enabled: true

evidence:
  expand_context: "adaptive" # none / neighbors / parent / adaptive
```

层级切分当前按字符数计长，`child_size` 与 `parent_size` 不应直接解释为精确 token 数。完整字段和安全配置见 [Zotero Agent Evidence 使用手册](docs/ZOTERO_AGENT_EVIDENCE_USER_MANUAL.md)。

配置文件支持 `${VAR}` 和 `${VAR:-default}` 环境变量占位符。启动前设置
`DEEPSEEK_API_KEY`（如启用视觉模型，再设置 `AZURE_OPENAI_API_KEY`），不要把真实密钥写入
YAML 或提交到版本库。`MODULAR_RAG_DATA_DIR` 控制 Chroma、BM25 和表格资产的运行目录，
`MODULAR_RAG_TRACE_DIR` 可单独控制 trace 目录；未设置时均使用仓库下的 `data/`。

生产部署时，推荐将上述数据目录指向本地可写的 runtime 目录（例如
`C:/rag_runtime/modular_rag/`），避免 SQLite/Chroma/BM25 文件受到同步、扫描或仓库权限策略影响。

---

## 使用指南

### 文档摄入

```bash
# 摄入单个 PDF
python scripts/ingest.py --path documents/report.pdf --collection my_docs

# 摄入目录下所有 PDF
python scripts/ingest.py --path documents/ --collection my_docs

# 学术论文模式（启用 GROBID 深度解析）
python scripts/ingest.py --path papers/ --collection research --paper-loader

# 强制重新处理（忽略已摄入记录）
python scripts/ingest.py --path documents/report.pdf --collection my_docs --force

# 使用自定义配置文件
python scripts/ingest.py --path documents/ --config custom_settings.yaml
```

### Zotero 文献同步

Zotero 同步是可选能力：需先启动 Zotero Desktop 并开启 Local API，然后在配置中启用 `sources.zotero.enabled`。同步仅读取指定收藏夹，不写入 Zotero。

```powershell
# 先预览新增、更新、跳过计划；不写入索引、状态或 Trace
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --dry-run

# 正式同步；--paper-loader 启用 GROBID-aware 论文解析
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --paper-loader
```

正式同步会生成不可覆盖的 manifest，并以 `sync_run_id` 关联运行级和文档级 Trace。建议连续运行两次：在附件没有变化时，第二次应没有新增或更新。详见 [Zotero Agent Evidence 使用手册](docs/ZOTERO_AGENT_EVIDENCE_USER_MANUAL.md)。

### 知识检索

```bash
# 基本查询
python scripts/query.py --query "Azure OpenAI 如何配置？" --collection my_docs

# 详细模式（显示 Dense/Sparse/Fusion/Rerank 各阶段结果）
python scripts/query.py --query "RRF 算法原理" --verbose

# 禁用重排序
python scripts/query.py --query "关键词搜索" --no-rerank

# 指定返回数量
python scripts/query.py --query "topological defect" --top-k 10
```

### 检索评测

评测 CLI 使用人工标注的 evidence chunk 计算 Hit@K、Recall@K、MRR、nDCG 等检索指标，并将测试集哈希、配置和逐查询结果保存为版本化产物：

```powershell
# 先校验 Golden Set 格式，不加载索引或模型
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --validate-only `
  --test-set tests\fixtures\golden_test_set.json

# 运行 Top-10 评测并保存产物
.\.venv\Scripts\python.exe scripts\evaluate.py `
  --test-set tests\fixtures\golden_test_set.json `
  --collection papers-v2 `
  --top-k 10 `
  --fail-on-errors
```

只有在相同 collection、语料/索引版本、模型和 Golden Set SHA256 下，指标才可横向比较。更多命令、质量门禁和 RRF/Cross-Encoder 消融步骤见 [评测使用手册](docs/EVALUATION_USER_MANUAL.md)。

### MCP Server 模式

在 Claude Desktop 或 VS Code Copilot 的配置中添加：

```json
{
  "mcpServers": {
    "modular-rag": {
      "command": "python",
      "args": ["-m", "src.mcp_server.server"],
      "cwd": "/path/to/modular-rag-mcp-server"
    }
  }
}
```

MCP 工具列表：

| 工具名 | 描述 |
|---|---|
| `query_knowledge_hub` | Hybrid / Section / Evidence 检索，返回 Evidence Bundle、引用定位及可选 Handoff 建议 |
| `list_collections` | 列出集合及可选的来源、Zotero 同步状态统计 |
| `get_document_summary` | 按项目文档 ID、Zotero Item Key 或 Citation Key 获取文档摘要 |
| `export_bibtex` | 为手工摄入或无 Zotero 插件的部署导出 BibTeX |

`query_knowledge_hub` 兼容原有调用，也支持 `retrieval_mode`（`hybrid` / `section` / `evidence`）、`expand_context` 和 `allow_fulltext_handoff`。Handoff 只返回外部 Agent 的下一步建议及 Attachment Key；项目本身不会读取 Zotero 全文。

### 可视化 Dashboard

```bash
streamlit run src/observability/dashboard/app.py
```

提供六个页面：

| 页面 | 功能 |
|---|---|
| **Overview** | 系统总览：文档数、Chunk 数、图片数、集合统计 |
| **Data Browser** | 浏览已摄入的文档和 Chunk 内容 |
| **Ingestion Manager** | 管理摄入任务，查看摄入历史 |
| **Ingestion Traces** | 摄入流水线的全链路 Trace 详情 |
| **Query Traces** | 查询 Trace 的可视化展示 |
| **Evaluation Panel** | 检索质量评估（hit@k / MRR / RAGAS） |

### 学术论文模式

本项目特别针对学术论文场景做了深度优化：

```bash
# 1. 确保 GROBID 运行中（localhost:8070）
docker run -d -p 8070:8070 lfoppiano/grobid:latest

# 2. 使用 paper-loader 模式摄入
python scripts/ingest.py \
  --path papers/research/ \
  --collection research_papers \
  --paper-loader

# 3. 查询论文内容
python scripts/query.py \
  --query "nonreciprocal interaction XY model" \
  --collection research_papers
```

**论文模式自动完成**：

1. **GROBID 解析 PDF** → 提取标题、作者、摘要、章节层次、图表标题和内容
2. **标题+摘要** → 合并为第一个 Chunk（超过 1000 字符时自动拆分）
3. **每个图表独立成 Chunk** → 标记 `chunk_type: "figure"` / `chunk_type: "table"`
4. **正文引用替换** → "Figure 1" → `[FIG_REF: fig_0]`，元数据记录 `linked_figures: ["fig_0"]`
5. **连带召回** → 检索到正文 Chunk 时，自动查找对应的图表 Chunk 合并返回

**数据流**：

```
PDF
 ↓ GROBID
TEI XML
 ↓ GrobidTEIParser
Paper(title, authors, abstract, sections, figures, tables)
 ↓ PaperPdfLoader._grobid_to_metadata()
Document.metadata (grobid_sections, grobid_figures, grobid_tables, ...)
 ↓ DocumentChunker (paper-aware)
┌──────────────────┬───────────────────┬─────────────────────────┐
│ Title+Abstract   │ Figure Chunks     │ Body Chunks             │
│ chunk_type:      │ chunk_type:       │ [FIG_REF: fig_0]        │
│ title_abstract   │ figure            │ linked_figures:[fig_0]  │
└──────────────────┴───────────────────┴─────────────────────────┘
 ↓                                                          ↓
 ChromaDB                                        QueryKnowledgeHubTool
                                                       ↓
                                            _resolve_linked_assets()
                                            get_by_metadata({"figure_id":"fig_0"})
                                                       ↓
                                            ResponseBuilder + linked_assets
                                            Markdown <details> 折叠展示图表
```

---

## 项目结构

```
modular-rag-mcp-server/
├── main.py                          # 项目入口
├── config/
│   └── settings.yaml                # 全局配置
├── scripts/
│   ├── ingest.py                    # 文档摄入 CLI
│   ├── query.py                     # 知识检索 CLI
│   ├── sync_zotero.py                # Zotero 只读增量同步 CLI
│   ├── evaluate.py                   # Golden Set 检索评测 CLI
│   ├── evaluate_retrieval_ablation.py # RRF / Cross-Encoder 消融
│   └── start_dashboard.py           # Dashboard 启动脚本
├── src/
│   ├── core/                        # 核心层
│   │   ├── types.py                 # Document / Chunk / RetrievalResult
│   │   ├── settings.py              # 配置加载
│   │   ├── query_engine/            # 检索引擎
│   │   │   ├── query_processor.py   # 查询预处理（分词/过滤）
│   │   │   ├── dense_retriever.py   # 稠密向量检索
│   │   │   ├── sparse_retriever.py  # BM25 稀疏检索
│   │   │   ├── hybrid_search.py     # 混合检索 + RRF 融合
│   │   │   ├── fusion.py            # RRF 融合算法
│   │   │   └── reranker.py          # 重排序 (Cross-Encoder/LLM)
│   │   ├── response/                # 响应构建
│   │   │   ├── response_builder.py  # Markdown + 引用 + 关联图表
│   │   │   ├── citation_generator.py
│   │   │   ├── multimodal_assembler.py
│   │   │   └── table_content.py
│   │   └── trace/                   # 可观测性 Trace
│   ├── ingestion/                   # 摄入层
│   │   ├── pipeline.py              # 摄入流水线编排器
│   │   ├── chunking/
│   │   │   ├── document_chunker.py  # 文档分块（含论文感知模式）
│   │   │   └── hierarchical_chunker.py # 可选 Parent–Child 分块
│   │   ├── transform/               # 数据转换
│   │   │   ├── chunk_refiner.py     # Chunk 精炼
│   │   │   ├── metadata_enricher.py # 元数据增强
│   │   │   ├── image_captioner.py   # 图片描述生成
│   │   │   └── table_extractor.py   # 表格提取
│   │   ├── embedding/               # 向量编码
│   │   │   ├── dense_encoder.py
│   │   │   ├── sparse_encoder.py
│   │   │   └── batch_processor.py
│   │   └── storage/                 # 存储
│   │       ├── vector_upserter.py   # ChromaDB 写入
│   │       ├── bm25_indexer.py      # BM25 索引
│   │       └── image_storage.py     # 图片存储索引
│   ├── libs/                        # 可插拔基础库
│   │   ├── loader/                  # 文档解析器
│   │   │   ├── pdf_loader.py        # PDF (MarkItDown + PyMuPDF)
│   │   │   ├── grobid_parser.py     # GROBID TEI XML 解析器
│   │   │   └── file_integrity.py    # 文件完整性 (SHA256)
│   │   ├── splitter/                # 文本切分器
│   │   ├── embedding/               # Embedding 适配器
│   │   ├── llm/                     # LLM 适配器
│   │   ├── reranker/                # 重排序适配器
│   │   └── vector_store/            # 向量数据库适配器
│   ├── mcp_server/                  # MCP Server
│   │   ├── server.py                # stdio transport
│   │   ├── protocol_handler.py      # 协议处理 & 工具注册
│   │   └── tools/                   # MCP Tools
│   │       ├── query_knowledge_hub.py
│   │       ├── get_document_summary.py
│   │       ├── list_collections.py
│   │       └── export_bibtex.py
│   ├── integrations/zotero/         # 本机 Zotero API、同步状态与来源映射
│   └── observability/               # 可观测性
│       ├── dashboard/               # Streamlit Dashboard
│       │   ├── app.py
│       │   └── pages/               # 6 个页面
│       ├── evaluation/              # 检索评估
│       └── logger.py
├── tests/
│   ├── unit/                        # 单元测试
│   ├── integration/                 # 集成测试
│   ├── e2e/                         # 端到端测试
│   └── fixtures/                    # 测试数据 & 样本 PDF
└── papers/                          # 论文 PDF 存放目录
```

---

## 测试

项目采用分层测试，覆盖解析、摄入、检索、评测、MCP 与来源集成链路。依赖真实 Zotero、GROBID、Ollama 或模型服务的测试与纯单元测试分离。

### 运行测试

```bash
# 运行所有单元测试（快速，无外部依赖）
pytest -m unit

# 运行集成测试（需要外部服务：GROBID / Ollama / Azure API）
pytest -m integration

# 运行端到端测试（完整流水线）
pytest -m e2e

# 跳过需要 LLM API 的测试
pytest -m "not llm"

# 跳过慢速测试
pytest -m "not slow"

# 运行全部并生成覆盖率报告
pytest --cov=src --cov-report=html
```

### 测试分层

| 层级 | 标记 | 说明 |
|---|---|---|
| **Unit** | `@pytest.mark.unit` | 快速，Mock 外部依赖 |
| **Integration** | `@pytest.mark.integration` | 需要真实服务 |
| **E2E** | `@pytest.mark.e2e` | 完整流水线验证 |

### 覆盖范围

| 模块 | 测试内容 |
|---|---|
| **Loader** | PDF 解析、GROBID TEI 解析、元数据提取、正则回退 |
| **Chunker** | 论文分块（标题+摘要 / 图表 / 正文引用）、引映射 |
| **Retrieval** | 混合检索、RRF 融合、重排序、连带资产解析 |
| **Evidence** | Parent/Neighbor 扩展、证据去重、Evidence Bundle、Handoff 决策 |
| **Evaluation** | Golden Set 校验、检索错误分离、指标聚合、质量门禁与消融产物 |
| **Zotero** | 只读 API、幂等同步、来源身份、状态表、manifest 与 Trace 关联 |
| **Response** | Markdown 渲染、引用生成、关联图表 `<details>` 展示 |
| **Pipeline** | 完整摄入→检索→连带召回→响应全链路 |

> 📊 **评估指南**：详细的评估方法论、Golden Test Set 构建、A/B 对比实验、CI/CD 集成方案，请参阅 [EVALUATION_GUIDE.md](EVALUATION_GUIDE.md)。

> 🧭 **Zotero Agent Evidence**：完整设计见 [技术方案](docs/ZOTERO_AGENT_EVIDENCE_PLATFORM_DESIGN.md)，落地状态见 [实施报告](docs/ZOTERO_AGENT_EVIDENCE_IMPLEMENTATION_REPORT.md)，实际配置与同步命令见 [使用手册](docs/ZOTERO_AGENT_EVIDENCE_USER_MANUAL.md)。

---

## 可扩展性

项目采用抽象接口 + 工厂模式 + 注册机制，添加新后端只需三步：

### 添加新的 LLM 后端

```python
# 1. 创建 src/libs/llm/your_llm.py，继承 BaseLLM
class YourLLM(BaseLLM):
    def generate(self, prompt: str, **kwargs) -> str:
        ...

# 2. 在 llm_factory.py 注册
LLMFactory.register_provider("your_provider", YourLLM)

# 3. 修改 config/settings.yaml
#    llm.provider: "your_provider"
```

### 添加新的向量数据库

```python
# 1. 创建实现，继承 BaseVectorStore
class YourStore(BaseVectorStore):
    def upsert(self, records): ...
    def query(self, vector, top_k, filters): ...

# 2. 注册到 VectorStoreFactory

# 3. 修改配置：vector_store.provider: "your_provider"
```

### 扩展论文解析

GROBID 解析器在 `src/libs/loader/grobid_parser.py`，可扩展：
- 公式提取（GROBID 已支持 `processFormula=true`）
- 参考文献结构化解析
- 自定义章节分类 / 过滤规则

---

## License

MIT License

---

<div align="center">
  <sub>Built with ❤️ as a learning-by-teaching RAG project</sub>
</div>

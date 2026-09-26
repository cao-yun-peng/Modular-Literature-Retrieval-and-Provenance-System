# Modular RAG MCP Server

面向学术论文的本地 RAG 工作台与 MCP 服务。项目把 PDF 解析、分块、向量化、混合检索、证据定位和研究草稿生成串成可检查的流程；React 工作台用于阅读原文和运行记录，Python CLI 与 MCP 工具可供其他客户端调用。

> 当前配置针对作者本机的 `papers48_baseline` 冻结语料。论文、向量索引、解析缓存和运行记录位于被 Git 忽略的 `data/`，**克隆仓库后不会得到这 48 篇论文或可直接查询的索引**。首次使用请按下文改为自己的集合并导入自己的 PDF。

## 能做什么

| 入口 | 能力 |
| --- | --- |
| [论文工作台](web/README.md) | 上传与处理 PDF；并排查看原 PDF、Markdown 和分块；查看任务事件；检索并生成带证据引用的回答。 |
| [研究 Agent](docs/RESEARCH_AGENT.md) | 基于当前本地集合进行多轮综述、时间线或问答，展示查询、证据和覆盖缺口，导出需人工核读的研究草稿。网页入口为 `/research`。 |
| [CLI](#命令行与-mcp) | 摄入文档、查询集合、运行研究任务、评测检索结果。 |
| [MCP Server](#命令行与-mcp) | 通过 stdio 暴露知识库查询、集合列表、文档摘要、BibTeX 导出和研究工具。 |

检索链路使用 Dense + BM25 召回、RRF 融合及可配置的重排序；论文链路保留章节、图注和来源定位。Zotero Local API 增量同步、层级证据扩展与 Streamlit Dashboard 也可单独使用。各组件的实际 provider 和开关以 [`config/settings.yaml`](config/settings.yaml) 为准。

## 快速开始

以下命令从仓库根目录执行。需要 Python 3.10+、[uv](https://docs.astral.sh/uv/)、Node.js 24 和 pnpm 11。当前默认论文解析使用 MinerU 在线服务，向量化使用 DashScope；摄入和研究生成会调用外部服务，可能产生费用。

```powershell
git clone https://github.com/cao-yun-peng/Modular-Literature-Retrieval-and-Provenance-System.git
cd Modular-Literature-Retrieval-and-Provenance-System
uv sync --frozen --extra web --extra dev
pnpm --dir web install --frozen-lockfile
pnpm --dir web build
```

1. 在 `config/settings.yaml` 中，把 `vector_store.collection_name` 从 `papers48_baseline` 改成自己的集合名（例如 `my_papers`），把 `vector_store.persist_directory` 改成新的本地目录（例如 `data/chroma-my-papers`）。不要向原冻结集合写入。
2. 在本机设置 `DASHSCOPE_API_KEY`；需要 LLM 重排序、回答或研究 Agent 时再设置 `DEEPSEEK_API_KEY`。可在仓库根目录创建被 Git 忽略的 `.env`，每行写 `KEY=value`。真实密钥不要提交到仓库。
3. 启动服务并打开 <http://127.0.0.1:8765/>：

```powershell
.venv/Scripts/python.exe scripts/start_workbench.py
```

从“文献库”上传自己的 PDF 并处理，再到“检索实验室”查询。工作台只监听本机。前端开发可另开终端运行 `pnpm --dir web dev`；API 文档在 <http://127.0.0.1:8765/docs>。更多启动、持久化和验证细节见 [工作台说明](web/README.md)。

### 默认配置与数据边界

`config/settings.yaml` 当前指定 MinerU Agent 解析、`cl100k_base` 计数与 2500 token 上限、DashScope `text-embedding-v3` 1024 维向量、Chroma + BM25 索引以及 DeepSeek LLM 重排序。MinerU 会把 PDF 发送到其服务；若文献不能上传，请先选用适合自己的解析方式和配置。

仓库不包含本机 48 篇基线及其测评数据。只有已有相应本地数据和冻结 manifest 的环境，才能运行 [基线校验](docs/PAPERS48_BASELINE.md) 与 [20 题测评](docs/PAPERS48_PILOT20.md)。这些实验记录不代表新克隆环境的测试结果。

## 命令行与 MCP

命令行摄入和查询应显式指定同一个集合。`--paper-loader` 启用论文解析路径；PDF 内容会上传至 MinerU 服务。

```powershell
.venv/Scripts/python.exe scripts/ingest.py --path papers/example.pdf --collection my_papers --paper-loader
.venv/Scripts/python.exe scripts/query.py --query "这篇论文的主要方法是什么？" --collection my_papers
.venv/Scripts/python.exe scripts/research.py --help
```

MCP 客户端可使用以下 stdio 配置，将路径替换为本机仓库的绝对路径：

```json
{
  "mcpServers": {
    "modular-rag": {
      "command": "C:/path/to/Modular-Literature-Retrieval-and-Provenance-System/.venv/Scripts/python.exe",
      "args": ["-m", "src.mcp_server.server"],
      "cwd": "C:/path/to/Modular-Literature-Retrieval-and-Provenance-System"
    }
  }
}
```

主要工具包括 `query_knowledge_hub`、`list_collections`、`get_document_summary`、`export_bibtex` 和 `research_topic`。研究 Agent 的网页版本只使用当前本地语料，不在网页中开放联网下载或 Zotero 写入；结果是研究草稿，需要核对引用和原文。接口与能力边界见 [研究 Agent 说明](docs/RESEARCH_AGENT.md)。

## 代码结构

```text
config/        配置与提示词
src/agents/    研究 Agent
src/core/      检索、响应、配置和证据处理
src/ingestion/ 解析后摄入、分块、编码和索引
src/libs/      模型、解析器、向量存储等适配器
src/mcp_server/ MCP 服务与工具
src/web_api/   FastAPI 工作台接口和持久化任务
web/          React/Vite 前端
scripts/      启动、摄入、查询、评测和数据管理命令
tests/        单元、集成和端到端测试
docs/         设计、操作与评测记录
```

## 验证

核心测试与前端构建可离线运行；浏览器端到端测试的前提见 [工作台说明](web/README.md)。以下测试命令不需要真实模型调用：

```powershell
.venv/Scripts/python.exe -m pytest tests/unit/test_web_research.py tests/unit/test_web_api.py tests/unit/test_research_agent.py tests/unit/test_research_interfaces.py tests/unit/test_research_acquisition.py -q
pnpm --dir web test
pnpm --dir web build
```

## 文档导航

- [工作台安装、运行与恢复](web/README.md)
- [研究 Agent：CLI、MCP 与网页接口](docs/RESEARCH_AGENT.md)
- [论文摄入指南](PAPER_INGESTION_GUIDE.md)
- [Zotero 同步与证据使用手册](docs/ZOTERO_AGENT_EVIDENCE_USER_MANUAL.md)
- [检索评测使用手册](docs/EVALUATION_USER_MANUAL.md)
- [48 篇论文的本地冻结基线](docs/PAPERS48_BASELINE.md)与[20 题测评草稿](docs/PAPERS48_PILOT20.md)
- [前端第三方资源说明](web/THIRD_PARTY.md)

## License

[MIT](LICENSE)

# Zotero 接入 Modular RAG MCP Server：实现与源码导读

> 适用范围：当前仓库中的 Zotero 只读同步、RAG 证据检索、Evidence Bundle 与 Agent Fulltext Handoff。
>
> 本文以当前源码为准。代码片段为便于理解的节选，省略了部分日志、异常处理和类型细节；完整实现请通过每节的源码链接查看。

## 1. 接入目标与边界

本项目接入 Zotero 不是为了替代 Zotero，而是将 Zotero 中维护的论文和引用身份接入现有 RAG 索引，让外部 Agent 能够完成两类任务：

1. 在未知或较大的文献集合中，通过 BM25 + Dense Retrieval + RRF 找到相关论文和局部证据；
2. 当局部证据不足，或者用户要求整体阅读论文时，将 Zotero Attachment Key 交给上层 Agent，由其调用 `zotero.fulltext`。

职责边界如下：

| 组件 | 负责内容 | 不负责内容 |
|---|---|---|
| Zotero Desktop | 文献条目、附件、标签、引用信息的事实源 | RAG 向量检索与排序 |
| 本项目 | 只读同步、分块、索引、证据定位、覆盖判断、Handoff 建议 | 修改 Zotero；代替 Zotero 插件读取全文 |
| 上层 Agent / Zotero 工具 | 根据 Handoff 使用 Attachment Key 读取全文、导出 BibTeX 或插入引用 | 把外部全文读取伪装成本项目行为 |

需要特别区分两个 Key：

- `zotero_item_key`：文献条目的 Zotero 身份，例如 `PXW99EKT`；
- `zotero_attachment_key`：某个 PDF 附件的 Zotero 身份，例如 `2JAZS9U8`，调用 `zotero.fulltext` 时主要使用它；
- `citation_key`：BibTeX 引用键，例如 `author_topic_2024`，可能为空，也不等于 Zotero Item Key。

## 2. 总体调用链

```text
Zotero Desktop Local API (127.0.0.1:23119)
  │
  │ GET 收藏夹条目、子附件、附件 file URL
  ▼
ZoteroLocalClient
  │
  │ SourceDocument + SourceAttachment
  ▼
ZoteroSyncService
  │
  │ 对比 source version + PDF SHA256
  │ 生成 ADD / UPDATE / SKIP 计划
  ▼
scripts/sync_zotero.py
  │
  │ to_ingestion_metadata()
  │ 调用 IngestionPipeline.run(..., source_metadata=...)
  ▼
Document → Child Chunks → Dense/BM25/SectionStore
  │
  │ 每个 Chunk 继承 Zotero Item/Attachment/Citation Key
  ▼
query_knowledge_hub MCP Tool
  │
  ├─ 返回 Evidence Bundle、引用与来源定位
  └─ 证据不足/需要通读 → recommended_next_action
                                  │
                                  ▼
                         Agent 调用 zotero.fulltext
```

## 3. 配置接入

配置入口是 [`config/settings.yaml`](../config/settings.yaml)，类型定义和校验位于 [`src/core/settings.py`](../src/core/settings.py#L277)。

推荐先使用新的目标 collection，例如 `papers-v2`，避免把新来源元数据或 Parent–Child schema 与旧索引混写。

```yaml
sources:
  zotero:
    enabled: true                         # [1] 允许同步 CLI 读取 Zotero
    base_url: "http://127.0.0.1:23119"    # [2] 只允许本机 Local API
    request_timeout_seconds: 10
    read_only: true                       # [3] 校验器强制要求 true
    sync_state_db: "${MODULAR_RAG_DATA_DIR:-data}/state/zotero_sync.sqlite3"
    allowed_attachment_roots: []          # [4] 生产环境建议配置明确目录

ingestion:
  hierarchical_chunking:
    enabled: true                         # [5] 新 collection 才建议开启
    section_store_db: "${MODULAR_RAG_DATA_DIR:-data}/db/section_store.sqlite3"
    corpus_schema_version: "2.0"

agent_handoff:
  enabled: true                           # [6] 允许生成全文阅读建议
  global_reading_handoff: true
  low_coverage_handoff: true
  low_score_threshold: 0.01
  max_recommended_documents: 3

evidence:
  include_zotero_identity: true           # [7] Evidence Bundle 保留两个 Zotero Key
  expand_context: "adaptive"
  max_context_characters: 6000
```

标注说明：

1. `sources.zotero.enabled` 只控制是否允许同步，不会自动扫描整个 Zotero Library；CLI 仍要求显式传入收藏夹 Key。
2. [`ZoteroLocalClient.__init__()`](../src/integrations/zotero/client.py#L41) 拒绝非 HTTP、非 loopback 地址。
3. [`validate_settings()`](../src/core/settings.py#L580) 强制 `read_only=true`。
4. 空 `allowed_attachment_roots` 会信任 Local API 返回的本机路径；生产环境应限制到 Zotero storage 或允许的 linked-file 根目录。
5. Parent–Child 切分会改变 Chunk ID 和 schema，因此应新建 collection 并重新评测。
6. Handoff 默认类型值是关闭；打开后也只产生建议，不执行全文读取。
7. 如果关闭 `include_zotero_identity`，查询响应与 citation 会移除 Zotero 身份，也无法生成可执行的全文 Handoff。

当前仓库 YAML 中 `sources.zotero.enabled=true`，但 Python 配置类型的默认值仍为 `false`；`hierarchical_chunking` 和 `agent_handoff` 当前 YAML 默认关闭。部署时应以实际加载的 YAML 为准。

## 4. 第一步：定义来源中立的数据契约

核心类型位于 [`src/integrations/zotero/models.py`](../src/integrations/zotero/models.py#L12)，上层抽象位于 [`src/integrations/source.py`](../src/integrations/source.py#L12)。

```python
@dataclass(frozen=True)
class SourceAttachment:
    key: str                  # [1] Zotero Attachment Key
    local_path: Path          # [2] Local API 返回并校验后的本机 PDF 路径
    version: str = ""
    content_type: str = "application/pdf"


@dataclass(frozen=True)
class SourceDocument:
    item_key: str             # [3] Zotero Item Key
    attachment: SourceAttachment
    title: str = ""
    creators: tuple[str, ...] = ()
    citation_key: str | None = None

    def to_ingestion_metadata(self) -> dict[str, Any]:
        return {
            "source_type": "zotero",
            "zotero_item_key": self.item_key,
            "zotero_attachment_key": self.attachment.key,
            "citation_key": self.citation_key,
            # ...
        }
```

这里没有让摄入流水线直接依赖 Zotero JSON。Zotero API 数据先被转换为稳定的 `SourceDocument`，再作为外部来源元数据进入通用 Pipeline。以后接入其他文献源时，可以复用同一边界。

## 5. 第二步：通过 Zotero Local API 发现 PDF

只读客户端是 [`ZoteroLocalClient`](../src/integrations/zotero/client.py#L28)。它只实现 GET 路径，没有创建、更新或删除 Zotero 条目的方法。

主要访问路径：

```text
GET /api/users/0/collections/{collection_key}/items/top
GET /api/users/0/items/{item_key}/children
GET /api/users/0/items/{attachment_key}/file/view/url
```

核心逻辑节选：

```python
def list_documents(self, collection_key=None):
    documents = []
    for item in self.list_top_level_items(collection_key):      # [1] 只扫描显式收藏夹
        for child in self.list_children(item_key):              # [2] 查询条目附件
            if child_data.get("itemType") != "attachment":
                continue
            if not self._is_pdf_attachment(child_data):         # [3] 只接入 PDF
                continue
            local_path = self.get_attachment_file_path(key)     # [4] 解析并校验 file URL
            documents.append(SourceDocument(...))               # [5] 转成来源中立对象
    return documents
```

安全控制集中在 [`get_attachment_file_path()`](../src/integrations/zotero/client.py#L139)：

- Local API 地址只能是 `127.0.0.1`、`localhost` 或 `::1`；
- 返回值必须是 `file:` URL；
- 本地路径必须存在且是文件；
- 配置了 `allowed_attachment_roots` 时，解析后的真实路径必须位于允许目录内。

## 6. 第三步：生成幂等同步计划

同步状态由 [`ZoteroSyncStateStore`](../src/integrations/zotero/state.py#L27) 保存到 SQLite，规划和执行由 [`ZoteroSyncService`](../src/integrations/zotero/sync_service.py#L49) 完成。

状态主键是：

```text
(item_key, attachment_key, target_collection)
```

因此同一个 PDF 可以同步到不同 collection，状态互不干扰。

计划判断位于 [`ZoteroSyncService.plan()`](../src/integrations/zotero/sync_service.py#L55)：

```python
file_sha256 = compute_sha256(document.attachment.local_path)  # [1] 比较真实 PDF 内容
current = state_store.get(item_key, attachment_key, collection)

if current 已同步
   and current.file_sha256 == file_sha256
   and current.source_version == zotero_item_and_attachment_version:
    action = SKIP                                              # [2] 内容和来源版本都没变
elif current is None:
    action = ADD                                               # [3] 首次同步
else:
    action = UPDATE                                            # [4] 元数据版本或文件内容变化
```

`execute()` 只在摄入成功后将状态写成 `synced`；异常会保存为 `error`。收藏夹中消失的附件只会在状态表中标记为 `inactive`，不会立即删除向量和原文件，从而避免不可恢复的数据删除。

## 7. 第四步：CLI 编排同步与摄入

用户入口是 [`scripts/sync_zotero.py`](../scripts/sync_zotero.py#L180)。它把 Local API、同步状态和既有 `IngestionPipeline` 串起来。

```python
settings = load_settings(args.config)
zotero = settings.sources.zotero

client = ZoteroLocalClient(...)                   # [1] 建立只读客户端
documents = client.list_documents(collection_key) # [2] 发现收藏夹 PDF

state_store = ZoteroSyncStateStore(state_db)
service = ZoteroSyncService(state_store)
plan = service.plan(documents, target_collection) # [3] ADD/UPDATE/SKIP

result = service.execute(plan, ingest)             # [4] 对需要处理的文档调用 Pipeline
```

CLI 参数：

| 参数 | 作用 |
|---|---|
| `--collection-key` | Zotero 收藏夹 Key，不是显示名称 |
| `--target-collection` | 项目内 Chroma/BM25 collection |
| `--dry-run` | 只生成计划，不写索引、状态、manifest 或 Trace |
| `--paper-loader` | 使用 GROBID-aware `PaperPdfLoader` |
| `--state-db` | 覆盖同步状态库路径 |
| `--manifest-dir` | 覆盖正式运行 manifest 目录 |

### 7.1 为什么 Pipeline 使用 `force=True`

[`get_pipeline()`](../scripts/sync_zotero.py#L276) 中刻意使用 `force=True`：

```python
pipeline = IngestionPipeline(
    settings,
    collection=args.collection,
    force=True,                 # [1]
    use_paper_loader=args.paper_loader,
)
```

原因是 Zotero 自己已有 collection-scoped 状态表。同步服务已经判定某个来源需要重建，就不能再被旧的全局 SHA256 历史表跳过，否则同一 PDF 同步到另一个 collection 时可能漏建索引。

### 7.2 元数据变化与 PDF 内容变化分开处理

[`ingest()`](../scripts/sync_zotero.py#L289) 有两条更新路径：

- SHA256 不变、Zotero 元数据版本变化：只调用向量库 `update_metadata()`，并同步更新 SectionStore 元数据，不重新 embedding；
- PDF SHA256 变化：重新执行完整摄入，然后按 `zotero_attachment_key` 清理旧 Chunk，保留新写入的 Chunk ID。

这个判断避免了“只改标题或标签，也重新做 PDF 解析和向量化”的浪费。

## 8. 第五步：把 Zotero 身份注入每个 Chunk

在 CLI 中，`SourceDocument` 先生成来源元数据：

```python
source_metadata = document.to_ingestion_metadata()
pipeline.run(
    str(document.attachment.local_path),
    source_metadata=source_metadata,             # [1] 传给通用摄入流水线
)
```

[`IngestionPipeline.run()`](../src/ingestion/pipeline.py#L225) 在 PDF Loader 产生 `Document` 后调用 [`attach_source_metadata()`](../src/ingestion/source_metadata.py#L20)：

```python
document = self.loader.load(str(file_path))
attach_source_metadata(document, source_metadata) # [1] 先写入 Document.metadata
chunks = self.chunker.split_document(document)    # [2] Chunker 将元数据复制到 Child
```

`attach_source_metadata()` 会拒绝外部来源覆盖以下 Pipeline 核心字段：

```text
source_path, source, images, chunk_index, source_ref, source_doc_id
```

因此 Zotero 可以补充来源身份，但不能破坏 Loader、Chunker 和索引依赖的内部契约。最终每个可检索 Chunk 都能携带：

```text
source_type=zotero
zotero_item_key
zotero_attachment_key
zotero_item_version
zotero_attachment_version
zotero_title / creators / year / DOI / tags
citation_key（如果存在）
```

## 9. 第六步：查询时返回可回溯证据

MCP 查询入口是 [`QueryKnowledgeHubTool.execute()`](../src/mcp_server/tools/query_knowledge_hub.py#L270)。调用方可以用 `zotero_item_keys` 将检索范围限制在指定 Zotero 文献内：

```json
{
  "query": "弱约束条件下的主要结果是什么？",
  "collection": "papers-v2",
  "retrieval_mode": "section",
  "zotero_item_keys": ["PXW99EKT"],
  "expand_context": "parent",
  "allow_fulltext_handoff": true
}
```

在混合检索、可选重排、去重和上下文扩展之后，查询工具调用 [`EvidenceBundleBuilder`](../src/core/response/evidence_bundle.py#L12) 序列化结果：

```python
evidence = {
    "evidence_id": result.chunk_id,                       # [1] 原始命中证据
    "text": result.text,
    "document_id": metadata.get("document_id"),
    "parent_id": metadata.get("parent_id"),
    "section_path": section_path,
    "page_start": page_start,
    "page_end": page_end,
    "citation_key": metadata.get("citation_key"),
    "zotero_item_key": metadata.get("zotero_item_key"),   # [2] 定位文献
    "zotero_attachment_key": metadata.get("zotero_attachment_key"), # [3] 全文交接
}
```

结构化引用由 [`CitationGenerator`](../src/core/response/citation_generator.py#L77) 生成。存在 `citation_key` 和页码时，可以产生类似 `[@author_topic_2024, pp. 7–8]` 的 Markdown 引用；缺失页码时不会伪造 locator。

## 10. 第七步：证据不足时生成 Fulltext Handoff

Handoff 规则位于 [`FulltextHandoffPolicy`](../src/core/query_engine/fulltext_handoff_policy.py#L35)。它是确定性策略，不调用模型，也不读取全文。

```python
def decide(query, results):
    if feature_disabled:
        return not_evaluated

    if not results:
        return insufficient_evidence              # [1] 没证据，也没有可验证附件可推荐

    if query 表达“全文/整体/主要贡献/完整论证”等意图:
        return needs_fulltext + Attachment Keys   # [2] 全局阅读意图

    if max(result.score) < low_score_threshold:
        return needs_fulltext + Attachment Keys   # [3] 低覆盖信号

    return evidence_available                     # [4] 局部证据可用，全文可选
```

生成的动作形态为：

```json
{
  "tool": "zotero.fulltext",
  "zotero_item_key": "PXW99EKT",
  "zotero_attachment_key": "2JAZS9U8",
  "required": false,
  "project_did_not_fetch_fulltext": true
}
```

两个字段是重要的诚实性边界：

- `required=false`：这是建议，不是服务端强制动作；
- `project_did_not_fetch_fulltext=true`：项目只完成了证据发现和交接，没有把外部 Agent 的全文读取算成自身行为。

当检索结果为空时，策略不会凭空构造 Attachment Key；只有返回结果携带真实 Zotero 身份时，才会推荐对应文档。

## 11. Trace 与 Manifest 如何串联

同步入口为每次运行生成一个 `sync_run_id`，同时创建：

- 一个 `scope=sync_run` 的运行级 Trace；
- 每个附件一个 `scope=document` 的文档级 ingestion Trace；
- 一个不可覆盖的 JSON manifest。

相关实现见 [`_document_trace()`](../scripts/sync_zotero.py#L130) 和 [`main()`](../scripts/sync_zotero.py#L180)。文档级 Trace 通过 `metadata.sync_run_id` 指向运行级 Trace，manifest 的每个附件条目又记录对应的 `trace_id`。

主要阶段包括：

```text
zotero_discovery
zotero_planning
load / split / transform / embed / upsert
zotero_metadata_update（仅元数据更新时）
zotero_sync
zotero_execution
zotero_manifest
```

正式运行默认将 manifest 写入：

```text
data/sync_manifests/zotero/<UTC timestamp>.json
```

Trace 写入 `observability.trace_file`。当 `observability.include_content=false` 时，Trace 保存 ID、状态、分数和耗时，不复制私人论文全文。

## 12. 实际接入步骤

### 12.1 准备 Zotero

1. 启动 Zotero Desktop；
2. 确认 Local API 已启用并监听 `127.0.0.1:23119`；
3. 确认目标条目存在本地可访问的 PDF 附件；
4. 获取收藏夹 Key，而不是收藏夹显示名称。

### 12.2 预览同步

```powershell
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --dry-run
```

预览只读取 Zotero 并输出 `ADD/UPDATE/SKIP` 计划；不会写索引、状态库、Trace 或 manifest。

### 12.3 正式同步

```powershell
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --paper-loader
```

退出码：

| 退出码 | 含义 |
|---:|---|
| `0` | 全部成功或合法跳过 |
| `1` | 至少一个附件摄入失败 |
| `2` | 配置、Zotero 连接或目标存储初始化失败 |

### 12.4 验证幂等

对同一收藏夹连续运行两次。附件和元数据未变化时，第二次应满足：

```text
added = 0
updated = 0
errors = 0
```

### 12.5 验证 MCP 查询与全文交接

先发起普通证据查询：

```json
{
  "query": "该论文如何定义非互易相互作用？",
  "collection": "papers-v2",
  "retrieval_mode": "evidence",
  "allow_fulltext_handoff": true
}
```

再发起整体阅读请求：

```json
{
  "query": "总结这篇论文的主要贡献和完整论证",
  "collection": "papers-v2",
  "zotero_item_keys": ["PXW99EKT"],
  "allow_fulltext_handoff": true
}
```

预期响应中出现：

```text
coverage.signal = needs_fulltext
recommended_next_action.zotero_attachment_key = <真实附件 Key>
project_did_not_fetch_fulltext = true
```

上层 Agent 再使用该 Attachment Key 调用 Zotero 工具的 `fulltext`。

## 13. 测试入口

核心单元测试：

```powershell
.\.venv\Scripts\python.exe -m pytest `
  tests\unit\test_zotero_integration.py `
  tests\unit\test_sync_zotero_observability.py `
  tests\unit\test_hierarchical_evidence.py -q
```

覆盖内容包括：

- Zotero Item/Attachment 到 `SourceDocument` 的映射；
- 非 loopback 地址拒绝；
- 外部来源元数据不能覆盖 Pipeline 保留字段；
- collection-scoped 幂等、PDF 内容变化和错误状态；
- 缺失附件只标记 inactive；
- 运行 Trace、文档 Trace、进度日志与 manifest 的关联；
- Evidence Bundle、Parent/Neighbor 扩展和 Handoff 决策。

真实 Zotero smoke test 必须在 Zotero Desktop 已启动且 Local API 可用时单独执行；默认单元测试使用 Stub/Fake，不读取个人 Zotero Library。

## 14. 关键源码索引

| 文件 | 作用 | 建议先看 |
|---|---|---|
| [`src/integrations/zotero/client.py`](../src/integrations/zotero/client.py) | Local API 读取、分页、PDF 筛选和路径安全 | `ZoteroLocalClient.list_documents()` |
| [`src/integrations/zotero/models.py`](../src/integrations/zotero/models.py) | 来源对象和 Chunk 元数据映射 | `SourceDocument.to_ingestion_metadata()` |
| [`src/integrations/zotero/state.py`](../src/integrations/zotero/state.py) | collection-scoped SQLite 同步状态 | `ZoteroSyncStateStore` |
| [`src/integrations/zotero/sync_service.py`](../src/integrations/zotero/sync_service.py) | ADD/UPDATE/SKIP 规划与执行 | `plan()`、`execute()` |
| [`scripts/sync_zotero.py`](../scripts/sync_zotero.py) | CLI 编排、Trace、manifest、Pipeline 调用 | `main()`、`ingest()` |
| [`src/ingestion/source_metadata.py`](../src/ingestion/source_metadata.py) | 安全注入外部来源元数据 | `attach_source_metadata()` |
| [`src/ingestion/pipeline.py`](../src/ingestion/pipeline.py) | 复用现有解析、分块、向量/BM25 存储 | `IngestionPipeline.run()` |
| [`src/mcp_server/tools/query_knowledge_hub.py`](../src/mcp_server/tools/query_knowledge_hub.py) | 查询、Zotero 文档范围、Evidence 与 Handoff 编排 | `QueryKnowledgeHubTool.execute()` |
| [`src/core/response/evidence_bundle.py`](../src/core/response/evidence_bundle.py) | 版本化证据响应 | `EvidenceBundleBuilder` |
| [`src/core/query_engine/fulltext_handoff_policy.py`](../src/core/query_engine/fulltext_handoff_policy.py) | 全文阅读意图与覆盖判断 | `FulltextHandoffPolicy.decide()` |
| [`src/mcp_server/tools/get_document_summary.py`](../src/mcp_server/tools/get_document_summary.py) | 按 Item Key 或 Citation Key 查文档 | `get_document_summary()` |
| [`src/mcp_server/tools/list_collections.py`](../src/mcp_server/tools/list_collections.py) | collection 来源和同步健康统计 | `list_collections()` |

## 15. 当前限制与后续改进

1. 当前来源适配器只同步 PDF 附件，不处理 Zotero Note、网页快照、EPUB 或其他格式。
2. `citation_key` 取决于 Zotero 返回的数据，可能为空；不能把 Item Key 当作 BibTeX Key。
3. Handoff 的低分阈值使用最终检索分数，只有通过目标数据集校准后才具有业务含义。
4. 收藏夹中消失的附件只标记 inactive，不自动物理删除历史索引；清理需要单独审核。
5. Parent–Child、去重和 conservative rerank 都应在新 collection 与冻结测试集上通过 Hit/Recall 门禁后再切换默认值。
6. 当前 Handoff 只输出工具建议；是否实际调用 `zotero.fulltext`、是否成功以及读取了多少内容，属于上层 Agent 的 Trace 范围。
7. 本地知识图谱尚未包含最新 Zotero 集成文件，理解和验收应以本文链接的当前源码和测试为准。

## 16. 一句话总结

Zotero 接入的关键不是“把 Zotero 变成另一个向量库”，而是建立一条可审计的数据链：**Zotero 提供文献身份与附件 → 同步服务幂等写入现有 RAG → 每条证据保留 Zotero Key → 证据不足时将真实 Attachment Key 交给上层 Agent 读取全文。**

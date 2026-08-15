# Zotero Agent Evidence 平台使用手册

> 适用版本：技术方案 v1.2 / Evidence Bundle v1.0  
> 默认策略：所有 Zotero、层级切分和 Handoff 功能均关闭，需显式开启。

## 1. 使用前准备

1. Zotero Desktop 正在运行，并允许本机 Local API；
2. 目标条目有可访问的本地 PDF 附件；
3. 已按项目原说明创建 `.venv` 并安装依赖；
4. Embedding、Chroma 和可选 GROBID 配置可用；
5. 记录 Zotero 收藏夹 Key，而不是收藏夹显示名称；
6. 新建 v2 目标 collection，例如 `papers-v2`，不要把新 schema 写进旧 `papers-v1`。

项目只连接 `http://127.0.0.1:23119`、`localhost` 或 `::1`，不会写入 Zotero，也不会代理 Zotero 插件的 fulltext/cite 操作。

## 2. 开启配置

编辑 `config/settings.yaml`：

```yaml
sources:
  zotero:
    enabled: true
    base_url: "http://127.0.0.1:23119"
    request_timeout_seconds: 10
    read_only: true
    sync_state_db: "${MODULAR_RAG_DATA_DIR:-data}/state/zotero_sync.sqlite3"
    allowed_attachment_roots: []

ingestion:
  hierarchical_chunking:
    enabled: true
    child_size: 350
    child_overlap: 50
    parent_size: 1200
    section_store_db: "${MODULAR_RAG_DATA_DIR:-data}/db/section_store.sqlite3"
    corpus_schema_version: "2.0"

agent_handoff:
  enabled: true
  global_reading_handoff: true
  low_coverage_handoff: true
  low_score_threshold: 0.01
  max_recommended_documents: 3

evidence:
  expand_context: "adaptive"
  include_score_breakdown: true
  include_zotero_identity: true
  max_context_characters: 6000
  deduplicate: true
```

生产环境建议把 `allowed_attachment_roots` 设置为 Zotero storage 和允许的 linked-file 根目录。空列表表示信任本机 Zotero Local API 返回的附件路径。

注意：项目现有递归 splitter 以字符数计长，所以 `child_size/parent_size` 当前是字符单位。先使用默认值做 v2 对照，再根据目标论文的中英文比例校准；不要把该数值直接当作精确 token 数。

## 3. 预览同步

```powershell
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --dry-run
```

预览会输出每个附件的 `add/update/skip`、原因、Item Key 和 Attachment Key，不输出 PDF 全文。

常用可选项：

- `--paper-loader`：使用现有 GROBID-aware 学术论文 loader；
- `--config path/to/settings.yaml`：指定配置；
- `--state-db path/to/state.sqlite3`：覆盖状态库；
- `--manifest-dir path/to/manifests`：覆盖正式同步 manifest 目录；
- `--base-url http://127.0.0.1:23119`：覆盖本机端点。

## 4. 正式同步

```powershell
.\.venv\Scripts\python.exe scripts\sync_zotero.py `
  --collection-key ABC123 `
  --target-collection papers-v2 `
  --paper-loader
```

退出码：

- `0`：全部成功或合法跳过；
- `1`：至少一个条目失败；
- `2`：配置无效、Zotero 不可用或目标存储无法初始化。

正式运行会在 `data/sync_manifests/zotero/` 写入不可覆盖的 JSON manifest。连续运行两次时，第二次的 `added + updated` 应为 `0`。

## 5. Agent 查询方式

### 5.1 旧调用

旧调用完全有效：

```json
{
  "query": "RRF 如何融合 Dense 和 BM25？",
  "top_k": 5,
  "collection": "papers-v2"
}
```

### 5.2 精确证据

```json
{
  "query": "实验中使用了什么速度场计算方法？",
  "collection": "papers-v2",
  "retrieval_mode": "evidence",
  "expand_context": "none",
  "allow_fulltext_handoff": true
}
```

### 5.3 已知论文内定位章节

```json
{
  "query": "弱约束条件下的主要结果是什么？",
  "collection": "papers-v2",
  "retrieval_mode": "section",
  "zotero_item_keys": ["PXW99EKT"],
  "expand_context": "parent"
}
```

### 5.4 跨库发现后全文深读

```json
{
  "query": "哪些论文系统比较了两种约束机制，并总结其整体论证？",
  "collection": "papers-v2",
  "retrieval_mode": "hybrid",
  "expand_context": "adaptive",
  "allow_fulltext_handoff": true
}
```

当 `coverage.signal=needs_fulltext` 时，外部 Agent 读取 `recommended_next_action.zotero_attachment_key`，再调用 Zotero 插件的 `fulltext`。`required=false` 表示这是建议，不是服务器已执行的动作；`project_did_not_fetch_fulltext=true` 必须保持为真。

如果用户一开始已经明确给出一篇或少量目标论文，并要求整体理解，Agent 应直接调用 Zotero 插件全文工具，无需先调用本项目检索。

## 6. Evidence Bundle 字段

| 字段 | 含义 |
|---|---|
| `schema_version` | 当前为 `1.0` |
| `retrieval` | 请求模式、实际模式、候选数、结果数和 fallback |
| `evidence[].text` | 可引用的原始 Child 文本 |
| `expanded_context` | 可选 Parent/Neighbor 上下文，不替代原证据 |
| `document_id/chunk_id/parent_id` | 项目内回溯链 |
| `section_path/page_start/page_end` | 章节和真实 locator；未知页码为 null |
| `zotero_item_key` | 定位 Zotero 文献条目 |
| `zotero_attachment_key` | 交给 Zotero fulltext 的附件身份 |
| `scores` | Dense/BM25（可得时）、RRF、Rerank、final；未知不伪造 |
| `coverage` | 可解释的证据覆盖信号 |
| `recommended_next_action` | 外部 Agent 下一步建议，可为 null |
| `citations` | Citation Key、locator 和 Markdown 渲染 |

## 7. 其他三个 MCP Tool

- `list_collections`：开启统计后返回 source type、文档数、Zotero 收藏夹 Key 和同步 active/inactive/error；
- `get_document_summary`：使用 `doc_id`、`zotero_item_key`、`citation_key` 三选一；
- `export_bibtex`：手工来源和无插件部署继续可用；Zotero 来源优先让 Agent 使用 Zotero 插件导出，避免两套 Citation Key。

## 8. 排序开关

默认保持旧行为：

```yaml
rerank:
  strategy: "replace"
```

完成冻结测试集对照后才尝试：

```yaml
rerank:
  strategy: "conservative"
  rrf_weight: 0.7
```

`conservative` 会对 RRF 和 Rerank 分数归一化后融合，并在结果元数据保留三组分数。只有 Hit@10、Recall@10 不下降时，才比较 MRR、nDCG 和延迟。

## 9. 回滚

1. 设置 `sources.zotero.enabled=false`，停止新同步；
2. 设置 `ingestion.hierarchical_chunking.enabled=false`；
3. 设置 `agent_handoff.enabled=false`；
4. 将 Agent 查询 collection 切回 `papers-v1`；
5. 设置 `rerank.enabled=false` 或 `strategy=replace`；
6. 保留 v2 collection、状态库和 manifest 诊断，不自动删除。

## 10. 常见错误

### Zotero source is disabled

设置 `sources.zotero.enabled=true`。CLI 尊重 feature flag，不会在关闭状态下偷偷同步。

### Cannot reach Zotero Local API

确认 Zotero 正在运行、Local API 已启用，并检查端口 `23119`。本实现拒绝非 loopback URL。

### attachment is outside allowed roots

将真实 Zotero storage 或 linked-file 根目录加入 `allowed_attachment_roots`，不要为了省事添加磁盘根目录。

### 查询有结果但没有 Parent 扩展

旧 v1 collection 没有 SectionStore 映射。请使用开启层级切分后新建并同步的 v2 collection；系统会对旧索引安全退化为 Child。

### Handoff 没有返回 Attachment Key

检查来源是否为 Zotero、同步元数据是否包含 Attachment Key、`agent_handoff.enabled` 和 `evidence.include_zotero_identity` 是否为 true。系统不会为手工 PDF 伪造 Zotero 身份。

## 11. 最小验收清单

1. 同一收藏夹连续同步两次，第二次无新增/更新；
2. 修改一个 PDF，只出现一个 update；
3. 修改标题/标签但不改 PDF，不发生重新 embedding；
4. 随机抽取证据核对 Item Key、Attachment Key、章节和页码；
5. 使用返回 Attachment Key 在外部 Zotero 插件执行 fulltext；
6. 关闭三个 feature flags 后，旧 MCP 查询仍通过；
7. 扩大人工问题集后执行 Current RRF、Parent–Child、Conservative Rerank 和 Discovery→Fulltext 对照，再决定生产默认值。

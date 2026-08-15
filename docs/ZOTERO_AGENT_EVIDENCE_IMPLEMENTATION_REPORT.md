# Zotero Agent Evidence 平台实施完成度报告

> 日期：2026-08-15  
> 对照方案：`docs/ZOTERO_AGENT_EVIDENCE_PLATFORM_DESIGN.md` v1.2  
> 结论：核心代码链路已完成；真实桌面环境和正式质量门禁仍需用户数据验收。

## 1. 完成结论

本次实现没有另起一套 RAG 服务，而是在原项目接口上完成了以下闭环：

```text
Zotero Local API（只读）
  → collection 范围发现与附件身份映射
  → collection-scoped SHA256/版本增量判断
  → 原有 IngestionPipeline
  → 可选 Parent–Child + Chroma/BM25/SectionStore
  → 原有 query_knowledge_hub
  → 去重/扩展/Evidence Bundle
  → 外部 Agent 可选调用 Zotero fulltext
```

现有四个 MCP Tool 名称不变。旧客户端仍可只传 `query`、`top_k`、`collection`；新参数都是可选字段。所有新功能默认关闭，避免旧 collection 混入 v2 chunk schema。

## 2. 分阶段完成度

| 阶段 | 状态 | 已落地内容 | 尚需外部验收 |
|---|---|---|---|
| Phase 0 契约和身份 | 完成 | Source 协议、Zotero Item/Attachment/Citation 身份、可选配置、来源元数据安全注入、SQLite 状态表 | 无 |
| Phase 1 最小同步闭环 | 代码完成 | Loopback-only Local API、指定收藏夹、PDF 附件、SHA256 幂等、元数据原位更新、内容变更安全替换、inactive 标记、manifest、CLI 退出码 | 在用户 Zotero 上做 smoke test |
| Phase 2 层级证据 | 完成 | Child/Parent、章节边界、页码 null 规则、前后邻接、独立 SectionStore、Parent/Neighbor/Adaptive 扩展、候选去重 | 用真实论文重建 `papers-v2` 并重标 chunk ID |
| Phase 3 Agent Handoff | 代码完成 | `hybrid/section/evidence`、文档范围、Evidence Bundle v1、Item/Attachment Key、全局阅读/低覆盖规则、Trace、项目不取全文 | 外部 Agent 使用返回 Attachment Key 调 Zotero 插件 `fulltext` |
| Phase 4 排序与评测 | 工程能力完成，质量门禁待执行 | Dynamic Candidate-K、保守 RRF/Rerank 融合开关、fallback 标记、既有 RRF/Cross-Encoder 消融框架 | 40–50 条开发集、至少 30 条冻结测试集和四方对照 |

## 3. 关键接口兼容处理

### 3.1 摄取接口

`IngestionPipeline.run()` 只新增可选 `source_metadata`。来源适配器不能覆盖 `source_path`、`images`、`chunk_index`、`source_ref` 等 Pipeline 自有字段；合法 Zotero 身份会在 loader 之后、chunking 之前注入，因此自动继承到所有 Child。

SHA256 兼容问题已单独处理：旧完整性表按文件全局去重，而 Zotero 状态按 `(item_key, attachment_key, target_collection)` 隔离。同步服务先自行判断增量，确需摄取时对原 Pipeline 使用 `force=True`，防止相同 PDF 在另一个 collection 被错误跳过。

### 3.2 更新安全性

- 版本和 SHA256 都不变：skip；
- Zotero 元数据变化、PDF SHA256 不变：只更新 Chroma/Section 来源元数据，不重新 embedding；
- PDF SHA256 变化：先完整写入新版本，成功后删除相同 Attachment Key 的旧向量，并删除旧 BM25/Section 映射；
- 新版本写入失败：旧版本仍保留；
- Zotero 中消失：状态标记 inactive，不自动物理删除索引。

### 3.3 MCP 响应

`MCPToolResponse` 保留原 `content/citations/metadata/isEmpty`，仅增加可空 `evidenceBundle`。结构化 citation 增加 Zotero 身份和真实 locator；页码未知时为 `null`，不会根据 chunk 顺序猜测。

`get_document_summary` 现在支持 Project Document ID、Zotero Item Key、Citation Key 三选一。Zotero 来源的摘要响应默认不返回本地绝对附件路径。

## 4. 主要实现文件

| 能力 | 文件 |
|---|---|
| 可选配置与 feature flags | `src/core/settings.py`、`config/settings.yaml` |
| Source/Zotero 契约 | `src/integrations/source.py`、`src/integrations/zotero/` |
| 同步 CLI | `scripts/sync_zotero.py` |
| 摄取连接与来源保护 | `src/ingestion/source_metadata.py`、`src/ingestion/pipeline.py` |
| Parent–Child | `src/ingestion/chunking/hierarchical_chunker.py` |
| SectionStore | `src/ingestion/storage/section_store.py` |
| 去重、扩展、handoff | `src/core/query_engine/evidence_deduplicator.py`、`context_expander.py`、`fulltext_handoff_policy.py` |
| Evidence Bundle | `src/core/response/evidence_bundle.py`、`citation_generator.py`、`response_builder.py` |
| MCP 编排 | `src/mcp_server/tools/query_knowledge_hub.py` |

## 5. 验证结果

全量单元测试：

```text
1356 passed, 1 skipped, 1 warning
```

跳过项为项目既有可选测试；唯一 warning 是 Windows 对 `tests/.tmp/pytest_cache` 的权限拒绝，不是功能失败。额外执行的摄取 Trace、查询 Trace、Hybrid Search 和接口回归均通过。

新增测试覆盖：

- collection-scoped 幂等、文件变更、错误状态和空收藏夹 inactive；
- Local API JSON 映射和 loopback 限制；
- Source 元数据不能破坏 loader 契约；
- Parent 跨章节分组、Neighbor、SectionStore round-trip；
- Evidence 去重、上下文扩展、Evidence Bundle locator；
- 全局阅读 handoff 只给建议且声明项目未读取全文；
- 旧查询调用仍返回正常结果；
- Conservative Fusion 同时保留 RRF、Rerank 和 final 分数。

## 6. 没有伪报完成的部分

以下门禁不能靠 mock 或 3 篇/12 问题开发集证明：

1. 用户 Zotero 是否已开启 Local API，实际附件路径是否可访问；
2. 返回的 Attachment Key 是否能被用户当前 Zotero 插件 `fulltext` 成功接受；
3. GROBID/PDF loader 在真实 10 篇论文上的页码和章节正确率；
4. Parent–Child 的 Hit@10/Recall@10 是否不低于 v1 RRF；
5. Conservative Rerank 是否同时守住 Hit/Recall 并改善 MRR/nDCG；
6. Zotero Direct、Current RRF、Parent–Child、Discovery→Fulltext 四方答案质量和 token 成本。

本次已对 `127.0.0.1:23119` 做只读连通性探测，结果为 Windows `ConnectionRefusedError (10061)`，说明验收时 Zotero Desktop/Local API 未监听该端口。探测没有读取正文、没有修改 Zotero；真实 smoke test 需在用户启动 Zotero 并开启 Local API 后按使用手册重跑。

因此生产默认仍是旧切分和 `replace` 排序。只有按照使用手册完成新 collection 和冻结测试集验收后，才应启用层级切分、handoff 或 conservative rerank。

当前底层 `RecursiveCharacterTextSplitter` 使用字符长度而非模型 tokenizer，因此 `child_size/parent_size` 也是字符单位。Parent–Child 关系和结构边界已经实现，但若要严格达到“200–400 tokens / 800–1500 tokens”，应先在目标中英文论文集上统计字符/token 比例，再校准配置或另行引入固定 tokenizer；本报告不把字符数伪称为精确 token 数。

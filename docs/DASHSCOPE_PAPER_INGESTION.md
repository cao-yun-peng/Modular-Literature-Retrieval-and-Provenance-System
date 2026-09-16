# DashScope 论文切分与图片说明接入记录

> 历史实验记录：2026-09-16 已将 v1/v2 运行数据和 `check_dashscope.py` 预览脚本列入清理清单，物理删除待确认。图片实验不是当前默认流程；`settings.dashscope.yaml` 仍供兼容性测试使用。当前状态见 [48 篇基线](PAPERS48_BASELINE.md) 与 [20 题测评](PAPERS48_PILOT20.md)。

MaterialPassport：experiment-agent / code-runner；2026-09-09；v1。
验证状态：本地软件测试与两个开发集小样本 API 调用 VERIFIED；全库新索引与新测评成绩 NOT_EXECUTED；图片说明 HUMAN_UNREVIEWED。

## 配置与行为

独立配置为 `config/settings.dashscope.yaml`，读取项目 `.env` 中的 `DASHSCOPE_API_KEY`、`QWEN_EMBEDDING_MODEL=text-embedding-v3` 和 `QWEN_VL_MODEL=qwen-vl-plus`。配置文件只保存环境变量占位符。进程中已设置的环境变量优先。默认 `config/settings.yaml` 尚未切换，原测评索引继续保留。

- 整篇正文按最多 **2,500 tokens** 切分，重叠 **200 tokens**，在窗口末尾优先寻找段落或句子边界；不固定每篇块数，不再先拆成短章节块。
- token 计数使用声明的本地代理 `cl100k_base`，不是 DashScope 专有 tokenizer。API 实际 token 用量单独记录，不用本地计数冒充计费用量。
- Embedding 为 **1,024 维**，每次最多 10 条输入，无原 Ollama 路径中的 1,000 字符截断。
- PDF 正文按物理页抽取；保存字符偏移、物理页范围及原文。该配置关闭 LLM 原文改写，规则清洗也跳过原文保护块。
- Qwen-VL 说明按内容、模型、提示词和参数缓存。仅向模型发送抽取图片及说明提示词。输出标为 `model_generated_unreviewed`，附图片哈希、物理页、模型、请求和用量记录。
- 图片说明作为独立元数据保存；向量检索、BM25 和重排输入附加最多 1,000 个本地 tokens 的说明。完整说明仍在 Evidence Bundle 的 `image_annotations` 中，`is_source_text=false`。
- 确定性原文证据评分不把 AI 说明算作 PDF 金标命中；扩展响应的 12,000 字符预算会计入这些说明的长度。

官方限制与接口依据：[Embedding 同步接口](https://help.aliyun.com/zh/model-studio/text-embedding-synchronous-api/)（v3 每条最多 8,192 tokens、每批最多 10 条）；[Qwen OpenAI 兼容接口](https://help.aliyun.com/zh/model-studio/qwen-api-via-openai-chat-completions)（图片可通过 Data URL 传入）。本地文本窗口与图片说明预算不等同于服务端 tokenizer 的硬限制，超限请求须报告失败，不能静默截掉正文。

## 实际验证结果

| 项目 | 结果 |
|---|---:|
| 全库本地预切分 | 48 篇 / 580 物理页 / 372 块 |
| 每篇块数中位数 / 范围 | 6 / 3–37 |
| 恰好 5–6 块 | 13/48 篇 |
| 4–8 块 | 33/48 篇 |
| 最大本地块 token 数 | 2,500 |
| 实际文本探针 | 2 段正文，均返回 1,024 维 |
| 文本本地 / API token 数 | 2,470 / 2,549；2,488 / 2,572 |
| 实际图片探针 | 2 张开发集论文图片，均返回完整说明 |
| 本次相关测试 | 233 + 107 = 340 项通过 |

固定大小无法让长度差异很大的论文都落在 5–6 块；当前配置让典型论文约 6 块。新索引尚未创建，因此 372 是本地切分预览数量，不能视为成功入库数量或检索效果提升。

测试覆盖 UTF-8 与 token 边界、跨页图片关联、原文保留、长文本传入 API、批次和返回顺序、错误维度、图片缓存、小图跳过、视觉元数据经 Chroma 往返、真实存储的模拟模型摄入链路、检索降级、评分、旧格式和 MCP stdio。测试中的 API 为模拟；另存的两个真实小样本调用是独立验证。

证据文件（均在仓库根目录下）：

- `data/paper_benchmark/v2-qwen2500/preview.json`：每篇块数及预览样本。
- `data/paper_benchmark/v2-qwen2500/preflight.json`：真实小样本结果及来源。
- `data/paper_benchmark/v2-qwen2500/preview_gallery.html`：两张图片和未审核模型说明。
- `data/paper_benchmark/v2-qwen2500/runtime/model_calls/calls.jsonl`：模型调用记录。
- `data/paper_benchmark/dashscope_final_tests.log` 与 `dashscope_store_rerank_tests.log`：340 项测试日志。

日志共记录两轮提示词验证的 8 次成功请求：Embedding 10,242 tokens，VL 2,008 tokens，总计 12,250 tokens。另一次初始连接探针使用 11 tokens，未计入该日志。不将 token 数换算为未经核验的金额。

## 图片处理范围与限制

本地抽取到 2,243 个位图片段。按最短边至少 64 像素且面积至少 16,384 像素过滤后，804 个图片出现项满足条件，按内容哈希去重为 762 张；1,439 个过小片段明确标为跳过。这是尺寸筛选，尚未人工判断图片是否为完整科学图。

当前提取的是 PDF 内嵌位图，可能缺少以矢量或正文形式绘制的坐标轴、图例与图注；两张实测样本也存在这种情况。提示词要求缺少轴或图例时说明物理量和数值尺度无法确定，不据颜色推断物理机制。纯矢量图、整图重组与直接曲线读数仍是能力缺口。MuPDF 的个别颜色空间警告保存在预览日志中。

图片和检索块按物理页重叠关联，标记为 `page_overlap`；这不是精确图框或段落级对齐。同页多个块可能共享图片，完整缓存与元数据保留此关系。

## 已准备的全库运行

运行清单为 `data/paper_benchmark/v2-qwen2500/upload_plan.json`，包含模型、目标地址、配置哈希、文本及图片数量。拟将全部 48 篇解析文本（372 块）以及约 762 张满足尺寸条件的独立图片发送至阿里云 DashScope 北京兼容接口，用于向量化和图片说明。正常缓存可避免重复图片调用，失败重试会单列实际调用次数。

自动审批审核拒绝了该全库上传命令，理由是完整语料与数百张图片的外发范围尚未获得明确授权。命令未执行；没有新索引，也没有新的检索基线。须取得针对上述内容与目的地的明确许可后再运行：

```powershell
.venv\Scripts\python.exe scripts/benchmark.py --root data/paper_benchmark/v2-qwen2500 --config config/settings.dashscope.yaml ingest
```

本地预览可独立运行：

```powershell
.venv\Scripts\python.exe scripts/check_dashscope.py preview
```

后续入库完成后须重建原文证据到新块的映射，再在开发集运行同条件检索及服务对照。新模型、切分、解析和图片说明同时变化的结果只能作为组合方案，不能直接解释为单项消融贡献。120 题人工审核与冻结仍按原测评计划执行，模型说明不能代替人工金标。

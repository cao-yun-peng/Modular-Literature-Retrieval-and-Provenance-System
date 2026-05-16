# 论文数据库加载指南

## 概述
该系统现在支持加载真实论文到数据库，并能自动提取论文元数据（标题、作者、DOI、章节、图表、参考文献等）。

## 快速开始

### 1. 准备论文文件

创建论文目录结构：
```bash
mkdir -p papers/research
# 将你的 PDF 论文放入该目录
# 例如：papers/research/paper1.pdf, papers/research/paper2.pdf
```

### 2. 使用脚本加载论文（推荐）

#### 方式 A：使用论文加载器（推荐）
启用 `--paper-loader` 参数以自动提取论文元数据：

```bash
# 单个论文
python scripts/ingest.py \
    --path papers/research/paper1.pdf \
    --collection research_papers \
    --paper-loader

# 整个目录
python scripts/ingest.py \
    --path papers/research/ \
    --collection research_papers \
    --paper-loader \
    --verbose
```

#### 方式 B：使用普通 PDF 加载器
不启用论文模式（只提取文本和图像）：

```bash
python scripts/ingest.py \
    --path papers/research/ \
    --collection research_papers
```

### 3. 查看处理进度

加上 `-v` 或 `--verbose` 参数查看详细输出：

```bash
python scripts/ingest.py \
    --path papers/ \
    --collection my_papers \
    --paper-loader \
    --verbose
```

## 加载器功能对比

| 功能 | 普通 PdfLoader | PaperPdfLoader（论文模式） |
|-----|---------------|--------------------------|
| 文本提取 | ✅ | ✅ |
| 图像提取 | ✅ | ✅ |
| 标题提取 | ❌ | ✅ |
| 作者提取 | ❌ | ✅ |
| DOI 提取 | ❌ | ✅ |
| 期刊/会议 | ❌ | ✅ |
| 章节提取 | ❌ | ✅ |
| 参考文献提取 | ❌ | ✅ |
| 表格提取 | ❌ | ✅ |
| 公式识别 | ❌ | ✅ |
| 目录生成 | ❌ | ✅ |

## Python API 方式

如果需要在 Python 代码中直接使用：

```python
from src.core.settings import load_settings
from src.ingestion.pipeline import IngestionPipeline
from src.core.trace import TraceContext

# 1. 加载设置
settings = load_settings("config/settings.yaml")

# 2. 创建管道（启用论文加载器）
pipeline = IngestionPipeline(
    settings=settings,
    collection="research_papers",
    force=False,
    use_paper_loader=True  # 关键参数：启用论文模式
)

# 3. 处理论文
result = pipeline.run("papers/paper1.pdf")

# 4. 查看结果
print(f"成功处理：{result.success}")
print(f"生成分块数：{result.chunk_count}")
print(f"提取图像数：{result.image_count}")
if not result.success:
    print(f"错误：{result.error}")
```

## 批量加载脚本示例

创建 `load_papers.py`：

```python
#!/usr/bin/env python
from pathlib import Path
from src.core.settings import load_settings
from src.ingestion.pipeline import IngestionPipeline
from src.core.trace import TraceContext

def batch_ingest_papers(papers_dir: str, collection: str = "papers"):
    """批量加载论文"""
    settings = load_settings("config/settings.yaml")
    pipeline = IngestionPipeline(
        settings=settings,
        collection=collection,
        use_paper_loader=True
    )
    
    papers = Path(papers_dir).glob("*.pdf")
    
    for i, paper_path in enumerate(papers, 1):
        print(f"\n[{i}] Processing: {paper_path.name}")
        
        trace = TraceContext(trace_type="ingestion")
        trace.metadata["source"] = str(paper_path)
        
        result = pipeline.run(str(paper_path), trace=trace)
        
        if result.success:
            print(f"✅ Success: {result.chunk_count} chunks")
        else:
            print(f"❌ Failed: {result.error}")

if __name__ == "__main__":
    batch_ingest_papers("papers/research", collection="research_papers")
```

运行：
```bash
python load_papers.py
```

## 输出和数据存储

加载完成后，数据存储在以下位置：

```
data/
├── db/
│   ├── chroma/              # 向量数据库
│   └── bm25/                # 搜索索引
├── images/
│   └── research_papers/     # 论文中提取的图像
└── tables/                  # 论文中提取的表格
```

## 常见问题

### Q1：需要真实的论文吗？
**A**：是的，需要真实的 PDF 论文文件。系统会自动处理 PDF 格式。

### Q2：加载速度如何？
**A**：取决于：
- 论文数量和大小
- 是否启用了 LLM 驱动的增强功能（元数据富化、标题生成等）
- 系统资源

平均单篇论文处理时间：1-5 秒（仅文本和图像提取）

### Q3：支持哪些 PDF 格式？
**A**：支持标准 PDF 格式。扫描版本的 PDF（图像 PDF）需要 OCR 支持。

### Q4：如何只提取文本不提取图像？
**A**：修改 `config/settings.yaml` 中的 `extract_images: false` 配置。

### Q5：如何重新处理已加载的论文？
**A**：使用 `--force` 参数强制重新处理：

```bash
python scripts/ingest.py \
    --path papers/paper1.pdf \
    --collection research_papers \
    --paper-loader \
    --force
```

## 验证加载结果

加载完成后验证数据：

```python
from src.libs.vector_store.vector_store_factory import VectorStoreFactory
from src.core.settings import load_settings

settings = load_settings("config/settings.yaml")
vector_store = VectorStoreFactory.create(
    store_type=settings.vector_store.type,
    collection_name="research_papers"
)

# 查询加载的论文
results = vector_store.search("machine learning", top_k=5)
print(f"检索到 {len(results)} 篇论文")
for result in results:
    print(f"- {result.metadata.get('source')}")
```

## 下一步

- 🔍 开始查询论文数据库
- 📊 评估检索效果
- 🎯 调整参数优化性能
- 💾 定期导出和备份数据


"""Read-only replay of the saved MinerU / DashScope validation artifacts."""
import json
from pathlib import Path

import streamlit as st

from src.core.settings import resolve_path


def render():
    st.header("📑 论文流程验证")
    st.caption("已保存的真实验证结果回放；浏览本页不会上传论文或调用模型 API。")
    root = resolve_path("output/mineru-qwen2500")

    def read(name):
        return json.loads((root / name).read_text(encoding="utf-8"))

    if not (root / "summary.json").exists():
        st.info("尚无验证结果，请先运行 scripts/validate_mineru_pipeline.py。")
        return
    summary = read("summary.json")
    ingestion = read("ingestion.json")
    chunks = read("chunks.json")
    document = read("document.json")
    st.subheader(Path(ingestion["file_path"]).stem)
    st.markdown("**MinerU 解析 → 结构整理与去重 → Token 分块 → 阿里云 Embedding → Chroma + BM25 → 引用问答**")
    cols = st.columns(4)
    cols[0].metric("分块数量", len(chunks))
    cols[1].metric("最大块 tokens", max(c["metadata"]["token_count"] for c in chunks))
    cols[2].metric("上限 / 目标重叠", "2500 / 200")
    cols[3].metric("向量数量 / 维度", f"{ingestion['vector_ids_count']} / {summary['embedding_dimensions']}")
    if summary.get("success"):
        st.success("解析、分块、向量入库、3 个检索问题及引用问答验证成功。")
    st.info("计数口径：cl100k_base。本文各章节均未超限，实际重叠为 0；长章节拆分时才使用 200 tokens 目标重叠。轻量解析接口不提供页码坐标或图像文件。")
    parse, split, embed, query = st.tabs(["1 · 解析与整理", "2 · 分块明细", "3 · 向量化与入库", "4 · 检索与回答"])
    with parse:
        metadata = document["metadata"]
        st.write("解析器：", metadata.get("parser"))
        st.write("本次使用缓存：", metadata.get("parser_cache_hit"))
        st.write("原始解析内容保留；独立摘要从正文分块中移除，图注单独组织。")
        st.write("章节：", metadata.get("toc", []))
        with st.expander("查看整理后的 Markdown"):
            st.code((root / "normalized.md").read_text(encoding="utf-8"), language="markdown")
    with split:
        rows = [{"编号": i + 1, "类型": c["metadata"].get("chunk_type"),
                 "章节": c["metadata"].get("section", ""),
                 "tokens": c["metadata"]["token_count"],
                 "实际重叠": c["metadata"].get("actual_overlap_tokens", 0)} for i, c in enumerate(chunks)]
        st.dataframe(rows, hide_index=True, use_container_width=True)
        selected = st.selectbox("选择分块查看全文", range(len(chunks)),
            format_func=lambda i: f"{i + 1} · {rows[i]['类型']} · {rows[i]['章节']} · {rows[i]['tokens']} tokens")
        st.code(chunks[selected]["text"], language="markdown")
        with st.expander("来源、图表关联和分块元数据"):
            st.json(chunks[selected]["metadata"])
    with embed:
        st.write("Embedding 模型：", summary["embedding_model"])
        st.write("Collection：", summary["collection"])
        st.write("完整文本提交，无字符截断；向量化前检查 token 上限。")
        if summary.get("full_input_hashes_verified"):
            st.success("全部分块的输入哈希与成功 API 请求记录匹配。")
        st.json(ingestion.get("stages", {}).get("encoding", {}))
        st.json(ingestion.get("stages", {}).get("storage", {}))
    with query:
        st.caption("以下为上次真实检索结果，不会在浏览时重新运行。")
        for i in range(1, 4):
            with st.expander(f"问题 {i} · 检索证据与来源"):
                result = read(f"query-{i}.json")
                st.text(result.get("content", ""))
                st.json(result.get("citations", []))
        st.subheader("已生成的引用回答")
        st.markdown((root / "answer.md").read_text(encoding="utf-8"))

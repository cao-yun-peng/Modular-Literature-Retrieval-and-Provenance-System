"""Tests for settings loading and validation."""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

from src.core.settings import SettingsError, get_table_storage_dir, load_settings


def _write_yaml(path: Path, content: str) -> None:
    path.write_text(textwrap.dedent(content).strip() + "\n", encoding="utf-8")


def test_load_settings_success(tmp_path: Path) -> None:
    config = """
    llm:
      provider: openai
      model: gpt-4o-mini
      temperature: 0.0
      max_tokens: 1024
    embedding:
      provider: openai
      model: text-embedding-3-small
      dimensions: 1536
    vector_store:
      provider: chroma
      persist_directory: C:/test_runtime/chroma
      collection_name: knowledge_hub
    retrieval:
      dense_top_k: 20
      sparse_top_k: 20
      fusion_top_k: 10
      rrf_k: 60
      reference_weight: 0.3
    rerank:
      enabled: false
      provider: none
      model: cross-encoder/ms-marco-MiniLM-L-6-v2
      top_k: 5
    evaluation:
      enabled: false
      provider: custom
      metrics:
        - hit_rate
        - mrr
    observability:
      log_level: INFO
      trace_enabled: true
      trace_file: C:/test_runtime/traces/traces.jsonl
      structured_logging: true
    ingestion:
      chunk_size: 1000
      chunk_overlap: 200
      splitter: recursive
      batch_size: 100
    """
    settings_path = tmp_path / "settings.yaml"
    _write_yaml(settings_path, config)

    settings = load_settings(settings_path)

    assert settings.llm.provider == "openai"
    assert settings.embedding.dimensions == 1536
    assert settings.vector_store.collection_name == "knowledge_hub"
    assert settings.retrieval.rrf_k == 60
    assert settings.retrieval.reference_weight == 0.3
    assert settings.rerank.provider == "none"
    assert settings.evaluation.metrics == ["hit_rate", "mrr"]
    assert settings.observability.log_level == "INFO"
    assert settings.ingestion is not None
    assert settings.sources.zotero.enabled is False
    assert settings.sources.zotero.read_only is True


def test_optional_zotero_source_settings_are_loaded(tmp_path: Path) -> None:
    config = """
    llm: {provider: openai, model: gpt-4o-mini, temperature: 0.0, max_tokens: 1024}
    embedding: {provider: openai, model: text-embedding-3-small, dimensions: 1536}
    vector_store: {provider: chroma, persist_directory: data/chroma, collection_name: knowledge_hub}
    retrieval: {dense_top_k: 20, sparse_top_k: 20, fusion_top_k: 10, rrf_k: 60, reference_weight: 0.3}
    rerank: {enabled: false, provider: none, model: none, top_k: 5}
    evaluation: {enabled: false, provider: custom, metrics: [hit_rate]}
    observability: {log_level: INFO, trace_enabled: true, trace_file: traces.jsonl, structured_logging: true}
    sources:
      zotero:
        enabled: true
        base_url: http://127.0.0.1:23119
        request_timeout_seconds: 12
        read_only: true
        sync_state_db: data/state/zotero.sqlite3
    """
    settings_path = tmp_path / "settings.yaml"
    _write_yaml(settings_path, config)

    settings = load_settings(settings_path)

    assert settings.sources.zotero.enabled is True
    assert settings.sources.zotero.request_timeout_seconds == 12.0
    assert settings.sources.zotero.sync_state_db == "data/state/zotero.sqlite3"


def test_zotero_source_rejects_write_mode(tmp_path: Path) -> None:
    config = """
    llm: {provider: openai, model: gpt-4o-mini, temperature: 0.0, max_tokens: 1024}
    embedding: {provider: openai, model: text-embedding-3-small, dimensions: 1536}
    vector_store: {provider: chroma, persist_directory: data/chroma, collection_name: knowledge_hub}
    retrieval: {dense_top_k: 20, sparse_top_k: 20, fusion_top_k: 10, rrf_k: 60, reference_weight: 0.3}
    rerank: {enabled: false, provider: none, model: none, top_k: 5}
    evaluation: {enabled: false, provider: custom, metrics: [hit_rate]}
    observability: {log_level: INFO, trace_enabled: true, trace_file: traces.jsonl, structured_logging: true}
    sources:
      zotero: {enabled: true, read_only: false}
    """
    settings_path = tmp_path / "settings.yaml"
    _write_yaml(settings_path, config)

    with pytest.raises(SettingsError, match="read_only"):
        load_settings(settings_path)


def test_missing_required_field_raises_error(tmp_path: Path) -> None:
    config = """
    llm:
      provider: openai
      model: gpt-4o-mini
      temperature: 0.0
      max_tokens: 1024
    embedding:
      model: text-embedding-3-small
      dimensions: 1536
    vector_store:
      provider: chroma
      persist_directory: C:/test_runtime/chroma
      collection_name: knowledge_hub
    retrieval:
      dense_top_k: 20
      sparse_top_k: 20
      fusion_top_k: 10
      rrf_k: 60
      reference_weight: 0.3
    rerank:
      enabled: false
      provider: none
      model: cross-encoder/ms-marco-MiniLM-L-6-v2
      top_k: 5
    evaluation:
      enabled: false
      provider: custom
      metrics:
        - hit_rate
    observability:
      log_level: INFO
      trace_enabled: true
      trace_file: C:/test_runtime/traces/traces.jsonl
      structured_logging: true
    """
    settings_path = tmp_path / "settings.yaml"
    _write_yaml(settings_path, config)

    with pytest.raises(SettingsError, match="embedding.provider"):
        load_settings(settings_path)


def test_default_settings_resolve_environment_and_portable_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_root = tmp_path / "runtime"
    trace_root = tmp_path / "trace-runtime"
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-deepseek-key")
    monkeypatch.setenv("MODULAR_RAG_DATA_DIR", str(data_root))
    monkeypatch.setenv("MODULAR_RAG_TRACE_DIR", str(trace_root))
    monkeypatch.delenv("AZURE_OPENAI_API_KEY", raising=False)

    settings = load_settings()

    assert settings.llm.api_key == "test-deepseek-key"
    assert settings.vision_llm is not None
    assert settings.vision_llm.api_key is None
    assert Path(settings.vector_store.persist_directory) == data_root / "chroma"
    assert Path(settings.observability.trace_file) == trace_root / "traces.jsonl"
    assert get_table_storage_dir("papers", settings) == (
        data_root / "tables" / "papers"
    ).resolve()


def test_missing_embedded_environment_variable_is_rejected(tmp_path: Path) -> None:
    config = """
    llm:
      provider: openai
      model: gpt-4o-mini
      temperature: 0.0
      max_tokens: 1024
    embedding:
      provider: openai
      model: text-embedding-3-small
      dimensions: 1536
    vector_store:
      provider: chroma
      persist_directory: ${MISSING_DATA_ROOT}/chroma
      collection_name: knowledge_hub
    retrieval:
      dense_top_k: 20
      sparse_top_k: 20
      fusion_top_k: 10
      rrf_k: 60
      reference_weight: 0.3
    rerank:
      enabled: false
      provider: none
      model: none
      top_k: 5
    evaluation:
      enabled: false
      provider: custom
      metrics: [hit_rate]
    observability:
      log_level: INFO
      trace_enabled: true
      trace_file: traces.jsonl
      structured_logging: true
    """
    settings_path = tmp_path / "settings.yaml"
    _write_yaml(settings_path, config)

    with pytest.raises(SettingsError, match="MISSING_DATA_ROOT"):
        load_settings(settings_path)

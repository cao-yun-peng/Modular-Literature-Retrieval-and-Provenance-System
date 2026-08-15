"""Configuration loading and validation for the Modular RAG MCP Server."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional, Union

import yaml

# ---------------------------------------------------------------------------
# Repo root & path resolution
# ---------------------------------------------------------------------------
# Anchored to this file's location: <repo>/src/core/settings.py → parents[2]
REPO_ROOT: Path = Path(__file__).resolve().parents[2]

# Default absolute path to settings.yaml
DEFAULT_SETTINGS_PATH: Path = REPO_ROOT / "config" / "settings.yaml"


def resolve_path(relative: Union[str, Path]) -> Path:
    """Resolve a repo-relative path to an absolute path.

    If *relative* is already absolute it is returned as-is.  Otherwise
    it is resolved against :data:`REPO_ROOT`.

    >>> resolve_path("config/settings.yaml")  # doctest: +SKIP
    PosixPath('/home/user/Modular-RAG-MCP-Server/config/settings.yaml')
    """
    p = Path(relative)
    if p.is_absolute():
        return p
    return (REPO_ROOT / p).resolve()


class SettingsError(ValueError):
    """Raised when settings validation fails."""


_ENV_PLACEHOLDER = re.compile(
    r"\$\{(?P<name>[A-Za-z_][A-Za-z0-9_]*)(?::-(?P<default>[^}]*))?\}"
)


def _expand_env_placeholders(value: Any, path: str = "settings") -> Any:
    """Recursively resolve ``${VAR}`` and ``${VAR:-default}`` values.

    A missing environment variable used as the complete value resolves to
    ``None`` so optional secrets can stay out of YAML files. Missing variables
    embedded inside a larger string are rejected unless they define a default.
    """
    if isinstance(value, dict):
        return {
            key: _expand_env_placeholders(item, f"{path}.{key}")
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _expand_env_placeholders(item, f"{path}[{index}]")
            for index, item in enumerate(value)
        ]
    if not isinstance(value, str):
        return value

    full_match = _ENV_PLACEHOLDER.fullmatch(value)
    if full_match:
        name = full_match.group("name")
        configured = os.environ.get(name)
        if configured:
            return configured
        default = full_match.group("default")
        return default if default is not None else None

    def replace(match: re.Match[str]) -> str:
        name = match.group("name")
        configured = os.environ.get(name)
        if configured:
            return configured
        default = match.group("default")
        if default is not None:
            return default
        raise SettingsError(
            f"Environment variable {name} referenced by {path} is not set"
        )

    return _ENV_PLACEHOLDER.sub(replace, value)


def _require_mapping(data: Dict[str, Any], key: str, path: str) -> Dict[str, Any]:
    value = data.get(key)
    if value is None:
        raise SettingsError(f"Missing required field: {path}.{key}")
    if not isinstance(value, dict):
        raise SettingsError(f"Expected mapping for field: {path}.{key}")
    return value


def _require_value(data: Dict[str, Any], key: str, path: str) -> Any:
    if key not in data or data.get(key) is None:
        raise SettingsError(f"Missing required field: {path}.{key}")
    return data[key]


def _require_str(data: Dict[str, Any], key: str, path: str) -> str:
    value = _require_value(data, key, path)
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"Expected non-empty string for field: {path}.{key}")
    return value


def _require_int(data: Dict[str, Any], key: str, path: str) -> int:
    value = _require_value(data, key, path)
    if not isinstance(value, int):
        raise SettingsError(f"Expected integer for field: {path}.{key}")
    return value


def _require_number(data: Dict[str, Any], key: str, path: str) -> float:
    value = _require_value(data, key, path)
    if not isinstance(value, (int, float)):
        raise SettingsError(f"Expected number for field: {path}.{key}")
    return float(value)


def _require_bool(data: Dict[str, Any], key: str, path: str) -> bool:
    value = _require_value(data, key, path)
    if not isinstance(value, bool):
        raise SettingsError(f"Expected boolean for field: {path}.{key}")
    return value


def _require_list(data: Dict[str, Any], key: str, path: str) -> List[Any]:
    value = _require_value(data, key, path)
    if not isinstance(value, list):
        raise SettingsError(f"Expected list for field: {path}.{key}")
    return value


def _optional_bool(
    data: Dict[str, Any], key: str, default: bool, path: str
) -> bool:
    value = data.get(key, default)
    if not isinstance(value, bool):
        raise SettingsError(f"Expected boolean for field: {path}.{key}")
    return value


def _optional_int(data: Dict[str, Any], key: str, default: int, path: str) -> int:
    value = data.get(key, default)
    if not isinstance(value, int) or isinstance(value, bool):
        raise SettingsError(f"Expected integer for field: {path}.{key}")
    return value


def _optional_number(
    data: Dict[str, Any], key: str, default: float, path: str
) -> float:
    value = data.get(key, default)
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        raise SettingsError(f"Expected number for field: {path}.{key}")
    return float(value)


def _optional_str(data: Dict[str, Any], key: str, default: str, path: str) -> str:
    value = data.get(key, default)
    if not isinstance(value, str) or not value.strip():
        raise SettingsError(f"Expected non-empty string for field: {path}.{key}")
    return value


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    temperature: float
    max_tokens: int
    # Azure/OpenAI-specific optional fields
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    azure_endpoint: Optional[str] = None
    deployment_name: Optional[str] = None
    # Ollama-specific optional fields
    base_url: Optional[str] = None


@dataclass(frozen=True)
class EmbeddingSettings:
    provider: str
    model: str
    dimensions: int
    # Azure-specific optional fields
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    azure_endpoint: Optional[str] = None
    deployment_name: Optional[str] = None
    # Ollama-specific optional fields
    base_url: Optional[str] = None


@dataclass(frozen=True)
class VectorStoreSettings:
    provider: str
    persist_directory: str
    collection_name: str


@dataclass(frozen=True)
class RetrievalSettings:
    dense_top_k: int
    sparse_top_k: int
    fusion_top_k: int
    rrf_k: int
    reference_weight: float
    candidate_multiplier: int = 2
    candidate_max: int = 100


@dataclass(frozen=True)
class RerankSettings:
    enabled: bool
    provider: str
    model: str
    top_k: int
    strategy: str = "replace"
    rrf_weight: float = 0.5


@dataclass(frozen=True)
class EvaluationSettings:
    enabled: bool
    provider: str
    metrics: List[str]


@dataclass(frozen=True)
class ObservabilitySettings:
    log_level: str
    trace_enabled: bool
    trace_file: str
    structured_logging: bool
    include_content: bool = False


@dataclass(frozen=True)
class VisionLLMSettings:
    enabled: bool
    provider: str
    model: str
    max_image_size: int
    api_key: Optional[str] = None
    api_version: Optional[str] = None
    azure_endpoint: Optional[str] = None
    deployment_name: Optional[str] = None
    base_url: Optional[str] = None


@dataclass(frozen=True)
class HierarchicalChunkingSettings:
    """Optional Parent–Child chunking settings; disabled for compatibility."""

    enabled: bool = False
    child_size: int = 350
    child_overlap: int = 50
    parent_size: int = 1200
    section_store_db: str = "data/db/section_store.sqlite3"
    corpus_schema_version: str = "2.0"


@dataclass(frozen=True)
class IngestionSettings:
    chunk_size: int
    chunk_overlap: int
    splitter: str
    batch_size: int
    chunk_refiner: Optional[Dict[str, Any]] = None  # 动态配置
    metadata_enricher: Optional[Dict[str, Any]] = None  # 动态配置
    hierarchical_chunking: HierarchicalChunkingSettings = field(
        default_factory=HierarchicalChunkingSettings
    )


@dataclass(frozen=True)
class AgentHandoffSettings:
    """Explainable Zotero full-text recommendation settings."""

    enabled: bool = False
    fulltext_provider: str = "zotero_plugin"
    global_reading_handoff: bool = True
    low_coverage_handoff: bool = True
    low_score_threshold: float = 0.01
    max_recommended_documents: int = 3


@dataclass(frozen=True)
class EvidenceSettings:
    """Evidence Bundle serialization and context expansion settings."""

    expand_context: str = "adaptive"
    include_score_breakdown: bool = True
    include_zotero_identity: bool = True
    require_source_locator: bool = False
    max_context_characters: int = 6000
    deduplicate: bool = False


@dataclass(frozen=True)
class ZoteroSettings:
    """Optional read-only connection settings for Zotero Local API."""

    enabled: bool = False
    base_url: str = "http://127.0.0.1:23119"
    request_timeout_seconds: float = 10.0
    read_only: bool = True
    sync_state_db: str = "data/state/zotero_sync.sqlite3"
    allowed_attachment_roots: tuple[str, ...] = ()


@dataclass(frozen=True)
class SourcesSettings:
    """Configuration for optional external document sources."""

    zotero: ZoteroSettings = field(default_factory=ZoteroSettings)


@dataclass(frozen=True)
class Settings:
    llm: LLMSettings
    embedding: EmbeddingSettings
    vector_store: VectorStoreSettings
    retrieval: RetrievalSettings
    rerank: RerankSettings
    evaluation: EvaluationSettings
    observability: ObservabilitySettings
    ingestion: Optional[IngestionSettings] = None
    vision_llm: Optional[VisionLLMSettings] = None
    sources: SourcesSettings = field(default_factory=SourcesSettings)
    agent_handoff: AgentHandoffSettings = field(default_factory=AgentHandoffSettings)
    evidence: EvidenceSettings = field(default_factory=EvidenceSettings)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "Settings":
        if not isinstance(data, dict):
            raise SettingsError("Settings root must be a mapping")

        llm = _require_mapping(data, "llm", "settings")
        embedding = _require_mapping(data, "embedding", "settings")
        vector_store = _require_mapping(data, "vector_store", "settings")
        retrieval = _require_mapping(data, "retrieval", "settings")
        rerank = _require_mapping(data, "rerank", "settings")
        evaluation = _require_mapping(data, "evaluation", "settings")
        observability = _require_mapping(data, "observability", "settings")

        ingestion_settings = None
        if "ingestion" in data:
            ingestion = _require_mapping(data, "ingestion", "settings")
            hierarchical = ingestion.get("hierarchical_chunking", {})
            if hierarchical is None:
                hierarchical = {}
            if not isinstance(hierarchical, dict):
                raise SettingsError(
                    "Expected mapping for field: ingestion.hierarchical_chunking"
                )
            hierarchical_settings = HierarchicalChunkingSettings(
                enabled=_optional_bool(
                    hierarchical, "enabled", False, "ingestion.hierarchical_chunking"
                ),
                child_size=_optional_int(
                    hierarchical, "child_size", 350, "ingestion.hierarchical_chunking"
                ),
                child_overlap=_optional_int(
                    hierarchical, "child_overlap", 50, "ingestion.hierarchical_chunking"
                ),
                parent_size=_optional_int(
                    hierarchical, "parent_size", 1200, "ingestion.hierarchical_chunking"
                ),
                section_store_db=_optional_str(
                    hierarchical,
                    "section_store_db",
                    "data/db/section_store.sqlite3",
                    "ingestion.hierarchical_chunking",
                ),
                corpus_schema_version=_optional_str(
                    hierarchical,
                    "corpus_schema_version",
                    "2.0",
                    "ingestion.hierarchical_chunking",
                ),
            )
            ingestion_settings = IngestionSettings(
                chunk_size=_require_int(ingestion, "chunk_size", "ingestion"),
                chunk_overlap=_require_int(ingestion, "chunk_overlap", "ingestion"),
                splitter=_require_str(ingestion, "splitter", "ingestion"),
                batch_size=_require_int(ingestion, "batch_size", "ingestion"),
                chunk_refiner=ingestion.get("chunk_refiner"),  # 可选配置
                metadata_enricher=ingestion.get("metadata_enricher"),  # 可选配置
                hierarchical_chunking=hierarchical_settings,
            )

        vision_llm_settings = None
        if "vision_llm" in data:
            vision_llm = _require_mapping(data, "vision_llm", "settings")
            vision_llm_settings = VisionLLMSettings(
                enabled=_require_bool(vision_llm, "enabled", "vision_llm"),
                provider=_require_str(vision_llm, "provider", "vision_llm"),
                model=_require_str(vision_llm, "model", "vision_llm"),
                max_image_size=_require_int(vision_llm, "max_image_size", "vision_llm"),
                api_key=vision_llm.get("api_key"),
                api_version=vision_llm.get("api_version"),
                azure_endpoint=vision_llm.get("azure_endpoint"),
                deployment_name=vision_llm.get("deployment_name"),
                base_url=vision_llm.get("base_url"),
            )

        sources_settings = SourcesSettings()
        if "sources" in data:
            sources = _require_mapping(data, "sources", "settings")
            zotero = sources.get("zotero", {})
            if zotero is None:
                zotero = {}
            if not isinstance(zotero, dict):
                raise SettingsError("Expected mapping for field: sources.zotero")

            enabled = zotero.get("enabled", False)
            read_only = zotero.get("read_only", True)
            timeout = zotero.get("request_timeout_seconds", 10.0)
            if not isinstance(enabled, bool):
                raise SettingsError("Expected boolean for field: sources.zotero.enabled")
            if not isinstance(read_only, bool):
                raise SettingsError("Expected boolean for field: sources.zotero.read_only")
            if not isinstance(timeout, (int, float)) or timeout <= 0:
                raise SettingsError(
                    "Expected positive number for field: sources.zotero.request_timeout_seconds"
                )

            base_url = zotero.get("base_url", "http://127.0.0.1:23119")
            sync_state_db = zotero.get("sync_state_db", "data/state/zotero_sync.sqlite3")
            allowed_roots = zotero.get("allowed_attachment_roots", [])
            if not isinstance(base_url, str) or not base_url.strip():
                raise SettingsError("Expected non-empty string for field: sources.zotero.base_url")
            if not isinstance(sync_state_db, str) or not sync_state_db.strip():
                raise SettingsError(
                    "Expected non-empty string for field: sources.zotero.sync_state_db"
                )
            if not isinstance(allowed_roots, list) or not all(
                isinstance(root, str) and root.strip() for root in allowed_roots
            ):
                raise SettingsError(
                    "Expected string list for field: sources.zotero.allowed_attachment_roots"
                )
            sources_settings = SourcesSettings(
                zotero=ZoteroSettings(
                    enabled=enabled,
                    base_url=base_url,
                    request_timeout_seconds=float(timeout),
                    read_only=read_only,
                    sync_state_db=sync_state_db,
                    allowed_attachment_roots=tuple(allowed_roots),
                )
            )

        agent_handoff_data = data.get("agent_handoff", {}) or {}
        if not isinstance(agent_handoff_data, dict):
            raise SettingsError("Expected mapping for field: settings.agent_handoff")
        agent_handoff_settings = AgentHandoffSettings(
            enabled=_optional_bool(agent_handoff_data, "enabled", False, "agent_handoff"),
            fulltext_provider=_optional_str(
                agent_handoff_data, "fulltext_provider", "zotero_plugin", "agent_handoff"
            ),
            global_reading_handoff=_optional_bool(
                agent_handoff_data, "global_reading_handoff", True, "agent_handoff"
            ),
            low_coverage_handoff=_optional_bool(
                agent_handoff_data, "low_coverage_handoff", True, "agent_handoff"
            ),
            low_score_threshold=_optional_number(
                agent_handoff_data, "low_score_threshold", 0.01, "agent_handoff"
            ),
            max_recommended_documents=_optional_int(
                agent_handoff_data, "max_recommended_documents", 3, "agent_handoff"
            ),
        )

        evidence_data = data.get("evidence", {}) or {}
        if not isinstance(evidence_data, dict):
            raise SettingsError("Expected mapping for field: settings.evidence")
        evidence_settings = EvidenceSettings(
            expand_context=_optional_str(
                evidence_data, "expand_context", "adaptive", "evidence"
            ),
            include_score_breakdown=_optional_bool(
                evidence_data, "include_score_breakdown", True, "evidence"
            ),
            include_zotero_identity=_optional_bool(
                evidence_data, "include_zotero_identity", True, "evidence"
            ),
            require_source_locator=_optional_bool(
                evidence_data, "require_source_locator", False, "evidence"
            ),
            max_context_characters=_optional_int(
                evidence_data, "max_context_characters", 6000, "evidence"
            ),
            deduplicate=_optional_bool(
                evidence_data, "deduplicate", False, "evidence"
            ),
        )

        settings = cls(
            llm=LLMSettings(
                provider=_require_str(llm, "provider", "llm"),
                model=_require_str(llm, "model", "llm"),
                temperature=_require_number(llm, "temperature", "llm"),
                max_tokens=_require_int(llm, "max_tokens", "llm"),
                api_key=llm.get("api_key"),
                api_version=llm.get("api_version"),
                azure_endpoint=llm.get("azure_endpoint"),
                deployment_name=llm.get("deployment_name"),
                base_url=llm.get("base_url"),
            ),
            embedding=EmbeddingSettings(
                provider=_require_str(embedding, "provider", "embedding"),
                model=_require_str(embedding, "model", "embedding"),
                dimensions=_require_int(embedding, "dimensions", "embedding"),
                api_key=embedding.get("api_key"),
                api_version=embedding.get("api_version"),
                azure_endpoint=embedding.get("azure_endpoint"),
                deployment_name=embedding.get("deployment_name"),
                base_url=embedding.get("base_url"),
            ),
            vector_store=VectorStoreSettings(
                provider=_require_str(vector_store, "provider", "vector_store"),
                persist_directory=_require_str(vector_store, "persist_directory", "vector_store"),
                collection_name=_require_str(vector_store, "collection_name", "vector_store"),
            ),
            retrieval=RetrievalSettings(
                dense_top_k=_require_int(retrieval, "dense_top_k", "retrieval"),
                sparse_top_k=_require_int(retrieval, "sparse_top_k", "retrieval"),
                fusion_top_k=_require_int(retrieval, "fusion_top_k", "retrieval"),
                rrf_k=_require_int(retrieval, "rrf_k", "retrieval"),
                reference_weight=_require_number(retrieval, "reference_weight", "retrieval"),
                candidate_multiplier=_optional_int(
                    retrieval, "candidate_multiplier", 2, "retrieval"
                ),
                candidate_max=_optional_int(retrieval, "candidate_max", 100, "retrieval"),
            ),
            rerank=RerankSettings(
                enabled=_require_bool(rerank, "enabled", "rerank"),
                provider=_require_str(rerank, "provider", "rerank"),
                model=_require_str(rerank, "model", "rerank"),
                top_k=_require_int(rerank, "top_k", "rerank"),
                strategy=_optional_str(rerank, "strategy", "replace", "rerank"),
                rrf_weight=_optional_number(rerank, "rrf_weight", 0.5, "rerank"),
            ),
            evaluation=EvaluationSettings(
                enabled=_require_bool(evaluation, "enabled", "evaluation"),
                provider=_require_str(evaluation, "provider", "evaluation"),
                metrics=[str(item) for item in _require_list(evaluation, "metrics", "evaluation")],
            ),
            observability=ObservabilitySettings(
                log_level=_require_str(observability, "log_level", "observability"),
                trace_enabled=_require_bool(observability, "trace_enabled", "observability"),
                trace_file=_require_str(observability, "trace_file", "observability"),
                structured_logging=_require_bool(observability, "structured_logging", "observability"),
                include_content=_optional_bool(
                    observability, "include_content", False, "observability"
                ),
            ),
            ingestion=ingestion_settings,
            vision_llm=vision_llm_settings,
            sources=sources_settings,
            agent_handoff=agent_handoff_settings,
            evidence=evidence_settings,
        )

        return settings


def validate_settings(settings: Settings) -> None:
    """Validate settings and raise SettingsError if invalid."""

    if not settings.llm.provider:
        raise SettingsError("Missing required field: llm.provider")
    if not settings.embedding.provider:
        raise SettingsError("Missing required field: embedding.provider")
    if not settings.vector_store.provider:
        raise SettingsError("Missing required field: vector_store.provider")
    if not settings.retrieval.rrf_k:
        raise SettingsError("Missing required field: retrieval.rrf_k")
    if not settings.rerank.provider:
        raise SettingsError("Missing required field: rerank.provider")
    if not settings.evaluation.provider:
        raise SettingsError("Missing required field: evaluation.provider")
    if not settings.observability.log_level:
        raise SettingsError("Missing required field: observability.log_level")
    if not settings.sources.zotero.read_only:
        raise SettingsError("sources.zotero.read_only must be true")
    if settings.retrieval.candidate_multiplier < 1:
        raise SettingsError("retrieval.candidate_multiplier must be >= 1")
    if settings.retrieval.candidate_max < 1:
        raise SettingsError("retrieval.candidate_max must be >= 1")
    if settings.rerank.strategy not in {"replace", "conservative"}:
        raise SettingsError("rerank.strategy must be replace or conservative")
    if not 0.0 <= settings.rerank.rrf_weight <= 1.0:
        raise SettingsError("rerank.rrf_weight must be between 0 and 1")
    hierarchical = settings.ingestion.hierarchical_chunking if settings.ingestion else None
    if hierarchical:
        if hierarchical.child_size <= 0 or hierarchical.parent_size <= 0:
            raise SettingsError("hierarchical chunk sizes must be positive")
        if not 0 <= hierarchical.child_overlap < hierarchical.child_size:
            raise SettingsError(
                "hierarchical child_overlap must be >= 0 and less than child_size"
            )
    if settings.agent_handoff.max_recommended_documents < 1:
        raise SettingsError("agent_handoff.max_recommended_documents must be >= 1")
    if settings.evidence.expand_context not in {"none", "neighbors", "parent", "adaptive"}:
        raise SettingsError("evidence.expand_context is invalid")
    if settings.evidence.max_context_characters < 1:
        raise SettingsError("evidence.max_context_characters must be positive")


def load_settings(path: str | Path | None = None) -> Settings:
    """Load settings from a YAML file and validate required fields.

    Args:
        path: Path to settings YAML.  Defaults to
            ``<repo>/config/settings.yaml`` (absolute, CWD-independent).
    """
    settings_path = Path(path) if path is not None else DEFAULT_SETTINGS_PATH
    if not settings_path.is_absolute():
        settings_path = resolve_path(settings_path)
    if not settings_path.exists():
        raise SettingsError(f"Settings file not found: {settings_path}")

    with settings_path.open("r", encoding="utf-8") as handle:
        data = yaml.safe_load(handle)

    expanded_data = _expand_env_placeholders(data or {})
    settings = Settings.from_dict(expanded_data)
    validate_settings(settings)
    return settings


def get_trace_file_path(settings: Optional[Settings] = None) -> Path:
    """Return the configured trace file path as an absolute path."""
    active_settings = settings or load_settings()
    return resolve_path(active_settings.observability.trace_file)


def get_bm25_index_dir(
    collection: str,
    settings: Optional[Settings] = None,
) -> Path:
    """Return the configured BM25 index directory for a collection.

    BM25 storage is colocated with the configured vector-store root so both
    dense and sparse indexes share the same writable runtime area.
    """
    active_settings = settings or load_settings()
    vector_root = resolve_path(active_settings.vector_store.persist_directory)
    return (vector_root.parent / "bm25" / collection).resolve()


def get_vector_store_persist_dir(settings: Optional[Settings] = None) -> Path:
    """Return the configured vector-store persist directory as an absolute path."""
    active_settings = settings or load_settings()
    return resolve_path(active_settings.vector_store.persist_directory)


def get_table_storage_dir(
    collection: str,
    settings: Optional[Settings] = None,
) -> Path:
    """Return the table-asset directory beside the configured indexes."""
    active_settings = settings or load_settings()
    vector_root = resolve_path(active_settings.vector_store.persist_directory)
    return (vector_root.parent / "tables" / collection).resolve()

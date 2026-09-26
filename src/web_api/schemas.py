from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class ApiProblem(Exception):  # noqa: N818 - shared HTTP problem contract
    def __init__(self, code: str, message: str, status: int = 400, retryable: bool = False):
        self.code, self.message, self.status, self.retryable = code, message, status, retryable


class ErrorDetail(BaseModel):
    code: str
    message: str
    retryable: bool = False


class ErrorResponse(BaseModel):
    error: ErrorDetail
    request_id: str


class DocumentOut(BaseModel):
    id: str
    content_hash: str
    title: str
    filename: str
    collection: str
    status: str
    created_at: str
    page_count: int
    chunk_count: int = 0
    latest_run_id: str | None = None
    latest_successful_run_id: str | None = None
    source_available: bool = True


class DocumentPage(BaseModel):
    items: list[DocumentOut]
    next_cursor: str | None = None


class RunOut(BaseModel):
    id: str
    document_id: str | None = None
    title: str
    collection: str
    kind: Literal["ingestion", "retrieval", "research"]
    status: Literal["queued", "running", "succeeded", "failed", "interrupted"]
    stage: str
    source: str
    created_at: str
    updated_at: str
    started_at: str | None = None
    finished_at: str | None = None
    config: dict[str, Any]
    result: dict[str, Any] | None = None
    error: ErrorDetail | None = None
    retry_of: str | None = None


class RunPage(BaseModel):
    items: list[RunOut]
    next_cursor: str | None = None


class RunEvent(BaseModel):
    event_id: int
    run_id: str
    stage: str
    status: str
    timestamp: str
    message: str = ""
    completed_units: int | None = None
    total_units: int | None = None
    elapsed_ms: float | None = None
    phase: Literal["started", "completed", "failed"] | None = None
    round: int | None = None
    query: str | None = None
    data: dict[str, Any] = Field(default_factory=dict)


class ResearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    topic: str = Field(min_length=1, max_length=2000)
    mode: Literal["review", "timeline", "answer"] = "review"
    collection: str | None = None
    max_rounds: int = Field(default=3, ge=1, le=5, strict=True)
    queries_per_round: int = Field(default=2, ge=1, le=3, strict=True)
    top_k: Literal[3, 5, 10] = 5

    @field_validator("topic")
    @classmethod
    def trim_topic(cls, value):
        if not value.strip():
            raise ValueError("topic must not be blank")
        return value.strip()


class ResearchEvidenceOut(BaseModel):
    id: str
    chunk_id: str
    document_id: str | None = None
    run_id: str | None = None
    title: str
    text: str
    year: str | None = None
    page: int | None = None
    section: str = ""
    excerpt_truncated: bool = False


class ResearchIterationOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    round: int
    queries: list[str] = Field(default_factory=list)
    new_evidence: int = 0
    sufficient: bool | None = None
    relevant_ids: list[str] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    next_queries: list[str] = Field(default_factory=list)


class ResearchResultOut(BaseModel):
    model_config = ConfigDict(json_schema_serialization_defaults_required=True)
    topic: str
    mode: str
    collection: str
    status: Literal["in_progress", "draft", "partial", "insufficient", "failed"] = "in_progress"
    stop_reason: str | None = None
    markdown: str = ""
    evidence: list[ResearchEvidenceOut] = Field(default_factory=list)
    iterations: list[ResearchIterationOut] = Field(default_factory=list)
    gaps: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    model_calls: int = 0
    source_count: int = 0
    run_options: dict[str, Any] = Field(default_factory=dict)
    model: str = "unknown"


class ChunkOut(BaseModel):
    id: str
    run_id: str
    document_id: str
    index: int
    text: str | None = None
    preview: str = ""
    chunk_type: str
    section: str
    token_count: int
    actual_overlap_tokens: int
    metadata: dict[str, Any] = Field(default_factory=dict)


class ChunkPage(BaseModel):
    items: list[ChunkOut]
    next_cursor: str | None = None


class IngestionRequest(BaseModel):
    document_id: str
    collection: str | None = None


class RetrievalRequest(BaseModel):
    query: str = Field(min_length=1, max_length=4000)
    collection: str | None = None
    document_ids: list[str] = Field(default_factory=list, max_length=100)
    top_k: Literal[3, 5, 10] = 5
    generate_answer: bool = True


class RetryRequest(BaseModel):
    stage: Literal["auto", "answer"] = "auto"


class EvidenceOut(BaseModel):
    index: int
    chunk_id: str
    document_id: str | None = None
    run_id: str | None = None
    title: str
    text: str
    section: str = ""
    scores: dict[str, float | None] = Field(default_factory=dict)
    linked_assets: list[ChunkOut] = Field(default_factory=list)


class RetrievalResultOut(BaseModel):
    query: str
    evidence: list[EvidenceOut] = Field(default_factory=list)
    answer: str | None = None
    answer_model: str | None = None
    answer_status: str = "not_requested"
    trace_id: str | None = None


class CollectionOut(BaseModel):
    name: str
    document_count: int
    chunk_count: int
    status: str


class PublicConfig(BaseModel):
    parser: str
    embedding_model: str
    embedding_dimensions: int
    embedding_configured: bool
    answer_model: str
    answer_configured: bool
    chunk_limit: int
    target_overlap: int
    tokenizer: str
    collection: str
    max_file_bytes: int
    max_pages: int

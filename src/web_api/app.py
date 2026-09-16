import asyncio
import json
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import urlparse

from fastapi import FastAPI, File, Header, Query, Request, UploadFile
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse

from src.core.settings import resolve_path
from src.libs.loader.mineru_agent import MinerUAgentClient

from .schemas import (
    ApiProblem,
    ChunkOut,
    ChunkPage,
    CollectionOut,
    DocumentOut,
    DocumentPage,
    ErrorResponse,
    IngestionRequest,
    PublicConfig,
    RetrievalRequest,
    RetrievalResultOut,
    RetryRequest,
    RunEvent,
    RunOut,
    RunPage,
)
from .service import TERMINAL, Workbench


def create_app(settings=None, root=None, *, start_worker=True):
    @asynccontextmanager
    async def lifespan(app):
        app.state.workbench = Workbench(settings, root)
        if start_worker:
            app.state.workbench.start()
        yield
        if start_worker:
            await asyncio.to_thread(app.state.workbench.close)

    app = FastAPI(
        title="论文 RAG 工作台 API",
        version="1.0.0",
        lifespan=lifespan,
        responses={
            400: {"model": ErrorResponse},
            404: {"model": ErrorResponse},
            409: {"model": ErrorResponse},
            422: {"model": ErrorResponse},
        },
    )

    def svc(request):
        return request.app.state.workbench

    def error(request, code, message, status, retryable=False):
        return JSONResponse(
            status_code=status,
            content={
                "error": {"code": code, "message": message, "retryable": retryable},
                "request_id": getattr(request.state, "request_id", uuid.uuid4().hex),
            },
        )

    @app.middleware("http")
    async def local_requests(request: Request, call_next):
        request.state.request_id = uuid.uuid4().hex
        if request.url.hostname not in ("127.0.0.1", "localhost", "testserver"):
            return error(request, "invalid_host", "仅支持本机访问", 403)
        origin = request.headers.get("origin")
        if origin and urlparse(origin).netloc not in (
            request.headers.get("host"),
            "127.0.0.1:5173",
            "localhost:5173",
        ):
            return error(request, "invalid_origin", "不允许跨站请求", 403)
        response = await call_next(request)
        response.headers["X-Request-ID"] = request.state.request_id
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response

    @app.exception_handler(ApiProblem)
    async def problem(request, exc):
        return error(request, exc.code, exc.message, exc.status, exc.retryable)

    @app.exception_handler(RequestValidationError)
    async def validation(request, exc):
        return error(request, "invalid_request", "请求参数不符合接口要求", 422)

    @app.exception_handler(Exception)
    async def unexpected(request, exc):
        import logging

        logging.getLogger(__name__).exception("API request failed", exc_info=exc)
        return error(request, "internal_error", "服务暂时无法完成请求，请检查本机日志", 500, True)

    def page(items, cursor, limit):
        try:
            offset = int(cursor or "0")
            if offset < 0:
                raise ValueError()
        except ValueError:
            raise ApiProblem("invalid_cursor", "分页游标无效", 422) from None
        return {
            "items": items[offset : offset + limit],
            "next_cursor": str(offset + limit) if len(items) > offset + limit else None,
        }

    @app.get("/api/v1/health", operation_id="health")
    def health():
        return {"status": "ok", "mode": "local"}

    @app.get("/api/v1/config/public", response_model=PublicConfig, operation_id="public_config")
    def public_config(request: Request):
        return svc(request).config()

    @app.get("/api/v1/collections", response_model=list[CollectionOut], operation_id="collections")
    def collections(request: Request):
        service = svc(request)
        name = service.collection(None)
        docs = [d for d in service.registry.all("documents") if d["collection"] == name]
        return [
            dict(
                name=name,
                document_count=len(docs),
                chunk_count=sum(d["chunk_count"] for d in docs),
                status=service.registry.health(name)["state"],
            )
        ]

    @app.get("/api/v1/documents", response_model=DocumentPage, operation_id="documents")
    def documents(
        request: Request,
        collection: str | None = None,
        q: str = "",
        status: str | None = None,
        cursor: str | None = None,
        limit: int = Query(20, ge=1, le=100),
    ):
        service = svc(request)
        collection = service.collection(collection)
        docs = [
            d
            for d in service.registry.all("documents")
            if d["collection"] == collection
            and (not status or d["status"] == status)
            and q.casefold() in (d["title"] + " " + d["filename"]).casefold()
        ]
        return page(sorted(docs, key=lambda d: d["created_at"], reverse=True), cursor, limit)

    @app.post(
        "/api/v1/documents/upload", response_model=DocumentOut, operation_id="upload_document"
    )
    async def upload_document(request: Request, file: UploadFile = File(...)):
        try:
            data = await file.read(MinerUAgentClient.document_max_bytes + 1)
            return await asyncio.to_thread(svc(request).upload, data, file.filename or "")
        finally:
            await file.close()

    @app.get("/api/v1/documents/{document_id}", response_model=DocumentOut, operation_id="document")
    def document(document_id: str, request: Request):
        return svc(request).registry.get("documents", document_id)

    @app.get("/api/v1/documents/{document_id}/source", operation_id="document_source")
    def source(document_id: str, request: Request):
        doc = svc(request).registry.get("documents", document_id)
        path = Path(doc["source_path"])
        if not path.exists():
            raise ApiProblem("source_missing", "原始文件不可用", 404)
        return FileResponse(
            path,
            media_type="application/pdf",
            filename=doc["filename"],
            content_disposition_type="inline",
        )

    @app.post(
        "/api/v1/ingestion-runs",
        response_model=RunOut,
        status_code=202,
        operation_id="create_ingestion",
    )
    def ingestion(
        body: IngestionRequest,
        request: Request,
        idempotency_key: str = Header(..., min_length=1, max_length=200),
    ):
        return svc(request).create_ingestion(body.model_dump(), idempotency_key)

    @app.post(
        "/api/v1/retrieval-runs",
        response_model=RunOut,
        status_code=202,
        operation_id="create_retrieval",
    )
    def retrieval(
        body: RetrievalRequest,
        request: Request,
        idempotency_key: str = Header(..., min_length=1, max_length=200),
    ):
        return svc(request).create_retrieval(body.model_dump(), idempotency_key)

    @app.get("/api/v1/runs", response_model=RunPage, operation_id="runs")
    def runs(
        request: Request,
        document_id: str | None = None,
        kind: str | None = None,
        cursor: str | None = None,
        limit: int = Query(20, ge=1, le=100),
    ):
        items = [
            r
            for r in svc(request).registry.all("runs")
            if (not document_id or r["document_id"] == document_id)
            and (not kind or r["kind"] == kind)
        ]
        return page(sorted(items, key=lambda r: r["created_at"], reverse=True), cursor, limit)

    @app.get("/api/v1/runs/{run_id}", response_model=RunOut, operation_id="run")
    def run(run_id: str, request: Request):
        return svc(request).registry.get("runs", run_id)

    @app.post(
        "/api/v1/runs/{run_id}/retry",
        response_model=RunOut,
        status_code=202,
        operation_id="retry_run",
    )
    def retry(
        run_id: str,
        body: RetryRequest,
        request: Request,
        idempotency_key: str = Header(..., min_length=1, max_length=200),
    ):
        return svc(request).retry(run_id, body.stage, idempotency_key)

    @app.get(
        "/api/v1/runs/{run_id}/event-history",
        response_model=list[RunEvent],
        operation_id="event_history",
    )
    def event_history(run_id: str, request: Request):
        service = svc(request)
        service.registry.get("runs", run_id)
        return service.registry.events(run_id)

    @app.get("/api/v1/runs/{run_id}/events", operation_id="run_events")
    async def events(run_id: str, request: Request, last_event_id: str = Header("0")):
        service = svc(request)
        service.registry.get("runs", run_id)
        try:
            after = int(last_event_id)
            if after < 0:
                raise ValueError()
        except ValueError:
            raise ApiProblem("invalid_event_id", "事件序号无效", 422) from None

        async def stream():
            current = after
            heartbeat = 0
            while not await request.is_disconnected():
                batch = await asyncio.to_thread(service.registry.events, run_id, current)
                for event in batch:
                    current = event["event_id"]
                    yield f"id: {current}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                state = await asyncio.to_thread(service.registry.get, "runs", run_id)
                if state["status"] in TERMINAL:
                    # Drain events committed between the event read and terminal state read.
                    tail = await asyncio.to_thread(service.registry.events, run_id, current)
                    for event in tail:
                        yield f"id: {event['event_id']}\nevent: progress\ndata: {json.dumps(event, ensure_ascii=False)}\n\n"
                    yield "event: done\ndata: {}\n\n"
                    break
                heartbeat += 1
                if heartbeat % 20 == 0:
                    yield ": heartbeat\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    @app.get("/api/v1/runs/{run_id}/artifacts/{kind}", operation_id="artifact")
    def artifact(run_id: str, kind: str, request: Request):
        allowed = {
            "raw_markdown": "raw.md",
            "normalized_markdown": "normalized.md",
            "structure": "structure.json",
        }
        if kind not in allowed:
            raise ApiProblem("unknown_artifact", "不支持的资源类型", 404)
        path = svc(request).folder(run_id) / allowed[kind]
        if not path.exists():
            raise ApiProblem("artifact_pending", "此阶段尚未生成结果", 404)
        return FileResponse(
            path,
            media_type="application/json" if kind == "structure" else "text/plain; charset=utf-8",
        )

    @app.get("/api/v1/runs/{run_id}/chunks", response_model=ChunkPage, operation_id="chunks")
    def chunks(
        run_id: str,
        request: Request,
        type: str | None = None,
        section: str | None = None,
        cursor: str | None = None,
        limit: int = Query(20, ge=1, le=100),
    ):
        service = svc(request)
        service.registry.get("runs", run_id)
        records = [
            c
            for c in service.registry.chunks(run_id)
            if (not type or c["chunk_type"] == type) and (not section or c["section"] == section)
        ]
        result = page(records, cursor, limit)
        result["items"] = [{**c, "text": None, "metadata": {}} for c in result["items"]]
        return result

    @app.get("/api/v1/runs/{run_id}/chunk-stats", operation_id="chunk_stats")
    def chunk_stats(run_id: str, request: Request):
        service = svc(request)
        service.registry.get("runs", run_id)
        chunks = service.registry.chunks(run_id)
        return {
            "count": len(chunks),
            "max_tokens": max((c["token_count"] for c in chunks), default=0),
            "sections": list(dict.fromkeys(c["section"] for c in chunks if c["section"])),
            "distribution": [{"index": c["index"] + 1, "tokens": c["token_count"]} for c in chunks],
        }

    @app.get("/api/v1/chunks/{chunk_id}", response_model=ChunkOut, operation_id="chunk")
    def chunk(chunk_id: str, run_id: str, request: Request):
        service = svc(request)
        service.registry.get("runs", run_id)
        for c in service.registry.chunks(run_id):
            if c["id"] == chunk_id:
                return c
        raise ApiProblem("chunk_missing", "未找到分块", 404)

    @app.get(
        "/api/v1/retrieval-runs/{run_id}/result",
        response_model=RetrievalResultOut,
        operation_id="retrieval_result",
    )
    def result(run_id: str, request: Request):
        return svc(request).retrieval_result(run_id)

    @app.get("/{path:path}", include_in_schema=False)
    def frontend(path: str):
        if path.startswith("api/"):
            raise ApiProblem("route_missing", "接口不存在", 404)
        root = resolve_path("web/dist")
        asset = (root / path).resolve()
        if not asset.is_relative_to(root.resolve()):
            raise ApiProblem("not_found", "资源不存在", 404)
        if asset.is_file():
            # Windows MIME registry may classify .mjs as text/plain. Module workers
            # must have a JavaScript MIME type, especially with nosniff enabled.
            media = {".mjs": "text/javascript", ".js": "text/javascript", ".css": "text/css"}.get(
                asset.suffix
            )
            return FileResponse(asset, media_type=media)
        if Path(path).suffix:
            raise ApiProblem("not_found", "资源不存在", 404)
        if not (root / "index.html").exists():
            return JSONResponse({"message": "请先在 web 目录运行 pnpm build"}, status_code=503)
        return FileResponse(root / "index.html")

    return app


app = create_app()

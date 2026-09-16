"""SQLite registry; each mutation and its event is committed before publication."""

import hashlib
import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

from .schemas import ApiProblem


def now():
    return datetime.now(timezone.utc).isoformat()


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def default_workbench_root(settings):
    """Keep the original registry and isolate subsequent configured collections."""
    import re
    from src.core.settings import resolve_path

    name = settings.vector_store.collection_name
    if name == "mineru_qwen2500":
        return resolve_path("data/web")
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", name)
    return resolve_path("data/web-collections") / (safe + "-" + hashlib.sha256(name.encode()).hexdigest()[:8])


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / "workbench.sqlite3"
        with self.connect() as db:
            db.executescript("""
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS documents (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS runs (id TEXT PRIMARY KEY, body TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY AUTOINCREMENT, run_id TEXT NOT NULL, body TEXT NOT NULL);
                CREATE INDEX IF NOT EXISTS events_run ON events(run_id,id);
                CREATE TABLE IF NOT EXISTS chunks (run_id TEXT, id TEXT, ordinal INTEGER, body TEXT NOT NULL, PRIMARY KEY(run_id,id));
                CREATE TABLE IF NOT EXISTS requests (key TEXT PRIMARY KEY, hash TEXT NOT NULL, run_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS collection_health (name TEXT PRIMARY KEY, state TEXT NOT NULL, run_id TEXT);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def all(self, table):
        assert table in ("documents", "runs")
        with self.connect() as db:
            return [json.loads(r["body"]) for r in db.execute(f"SELECT body FROM {table}")]

    def get(self, table, key):
        assert table in ("documents", "runs")
        with self.connect() as db:
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
        if not row:
            raise ApiProblem("not_found", "未找到请求的资源", 404)
        return json.loads(row["body"])

    def put(self, table, record):
        assert table in ("documents", "runs")
        with self.connect() as db:
            db.execute(
                f"INSERT OR REPLACE INTO {table} VALUES (?,?)",
                (record["id"], json.dumps(record, ensure_ascii=False)),
            )

    def update(self, table, key, **changes):
        assert table in ("documents", "runs")
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(f"SELECT body FROM {table} WHERE id=?", (key,)).fetchone()
            if not row:
                raise ApiProblem("not_found", "未找到资源", 404)
            record = {**json.loads(row["body"]), **changes}
            db.execute(
                f"UPDATE {table} SET body=? WHERE id=?",
                (json.dumps(record, ensure_ascii=False), key),
            )
        return record

    def create_run(self, kind, payload, config, key, *, title, document_id=None, retry_of=None):
        request_hash = digest([kind, payload, config, retry_of])
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            old = db.execute("SELECT * FROM requests WHERE key=?", (key,)).fetchone()
            if old:
                if old["hash"] != request_hash:
                    raise ApiProblem("idempotency_conflict", "同一幂等键不能用于不同请求", 409)
                return json.loads(
                    db.execute("SELECT body FROM runs WHERE id=?", (old["run_id"],)).fetchone()[
                        "body"
                    ]
                )
            if kind == "ingestion":
                for row in db.execute("SELECT body FROM runs"):
                    existing = json.loads(row["body"])
                    if (
                        existing["kind"] == kind
                        and existing["document_id"] == document_id
                        and existing["collection"] == payload["collection"]
                        and existing["config"] == config
                        and existing["status"] in ("queued", "running", "succeeded")
                    ):
                        db.execute(
                            "INSERT INTO requests VALUES (?,?,?)",
                            (key, request_hash, existing["id"]),
                        )
                        return existing
            stamp = now()
            run = dict(
                id="run_" + uuid.uuid4().hex,
                kind=kind,
                payload=payload,
                config=config,
                document_id=document_id,
                title=title,
                collection=payload["collection"],
                source="live",
                status="queued",
                stage="queued",
                created_at=stamp,
                updated_at=stamp,
                started_at=None,
                finished_at=None,
                error=None,
                result=None,
                retry_of=retry_of,
            )
            db.execute(
                "INSERT INTO runs VALUES (?,?)", (run["id"], json.dumps(run, ensure_ascii=False))
            )
            db.execute("INSERT INTO requests VALUES (?,?,?)", (key, request_hash, run["id"]))
            event = dict(
                run_id=run["id"],
                stage="queued",
                status="queued",
                timestamp=stamp,
                message="任务已排队",
            )
            db.execute(
                "INSERT INTO events(run_id,body) VALUES (?,?)", (run["id"], json.dumps(event))
            )
        return run

    def event(self, run_id, stage, status="running", message="", **detail):
        stamp = now()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT body FROM runs WHERE id=?", (run_id,)).fetchone()
            run = json.loads(row["body"])
            run.update(stage=stage, status=status, updated_at=stamp)
            if status == "running" and not run.get("started_at"):
                run["started_at"] = stamp
            if status in ("succeeded", "failed", "interrupted"):
                run["finished_at"] = stamp
            event = dict(
                run_id=run_id,
                stage=stage,
                status=status,
                timestamp=stamp,
                message=message,
                **detail,
            )
            db.execute(
                "UPDATE runs SET body=? WHERE id=?", (json.dumps(run, ensure_ascii=False), run_id)
            )
            cursor = db.execute(
                "INSERT INTO events(run_id,body) VALUES (?,?)",
                (run_id, json.dumps(event, ensure_ascii=False)),
            )
            event["event_id"] = cursor.lastrowid
        return event

    def events(self, run_id, after=0):
        with self.connect() as db:
            return [
                {**json.loads(row["body"]), "event_id": row["id"]}
                for row in db.execute(
                    "SELECT * FROM events WHERE run_id=? AND id>? ORDER BY id", (run_id, after)
                )
            ]

    def save_chunks(self, run_id, chunks):
        with self.connect() as db:
            db.execute("DELETE FROM chunks WHERE run_id=?", (run_id,))
            db.executemany(
                "INSERT INTO chunks VALUES (?,?,?,?)",
                [
                    (run_id, c["id"], i, json.dumps(c, ensure_ascii=False))
                    for i, c in enumerate(chunks)
                ],
            )

    def chunks(self, run_id):
        with self.connect() as db:
            return [
                json.loads(r["body"])
                for r in db.execute(
                    "SELECT body FROM chunks WHERE run_id=? ORDER BY ordinal", (run_id,)
                )
            ]

    def health(self, collection):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM collection_health WHERE name=?", (collection,)
            ).fetchone()
        return dict(row) if row else dict(name=collection, state="ready", run_id=None)

    def set_health(self, collection, state, run_id=None):
        with self.connect() as db:
            db.execute(
                "INSERT OR REPLACE INTO collection_health VALUES (?,?,?)",
                (collection, state, run_id),
            )

    def recover(self):
        for run in self.all("runs"):
            if run["status"] in ("running", "queued"):
                self.update(
                    "runs",
                    run["id"],
                    error=dict(
                        code="interrupted",
                        message="服务重启，任务已中断；可显式重试",
                        retryable=True,
                    ),
                )
                self.event(run["id"], run["stage"], "interrupted", "服务重启，等待显式重试")
                if run["kind"] == "ingestion" and run["document_id"]:
                    self.update("documents", run["document_id"], status="interrupted")
        with self.connect() as db:
            db.execute("UPDATE collection_health SET state='needs_repair' WHERE state='writing'")

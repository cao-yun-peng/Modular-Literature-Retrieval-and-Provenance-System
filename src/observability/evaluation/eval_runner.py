"""Evaluation runner for batch quality assessment.

EvalRunner reads a golden test set, runs HybridSearch for each test case,
optionally generates answers, then invokes the configured Evaluator(s) to
produce a structured evaluation report.

Design Principles:
- Config-Driven: Evaluator selected via settings.yaml.
- Observable: Produces EvalReport with per-query details.
- Decoupled: Works with any BaseEvaluator implementation.
"""

from __future__ import annotations

import json
import hashlib
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from src.libs.evaluator.base_evaluator import BaseEvaluator

logger = logging.getLogger(__name__)


class RetrievalError(RuntimeError):
    """Raised when retrieval infrastructure fails for an evaluation query."""


@dataclass
class GoldenTestCase:
    """A single evaluation test case from the golden test set.

    Attributes:
        query: The test query string.
        expected_chunk_ids: Ground-truth chunk IDs for IR metrics.
        expected_sources: Ground-truth source file names (optional).
        reference_answer: Reference answer text for LLM-as-Judge (optional).
    """

    query: str
    case_id: Optional[str] = None
    query_type: Optional[str] = None
    collection: Optional[str] = None
    expected_chunk_ids: List[str] = field(default_factory=list)
    expected_sources: List[str] = field(default_factory=list)
    reference_answer: Optional[str] = None

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> GoldenTestCase:
        query = data.get("query", data.get("question", ""))
        return cls(
            query=query,
            case_id=data.get("id") or data.get("query_id"),
            query_type=data.get("query_type"),
            collection=data.get("collection"),
            expected_chunk_ids=data.get(
                "expected_chunk_ids", data.get("supporting_chunk_ids", [])
            ),
            expected_sources=data.get("expected_sources", []),
            reference_answer=data.get("reference_answer", data.get("answer")),
        )


@dataclass
class QueryResult:
    """Result of evaluating a single test case.

    Attributes:
        query: The test query.
        retrieved_chunk_ids: IDs of chunks actually retrieved.
        generated_answer: The generated answer (if applicable).
        metrics: Evaluation metrics for this query.
        elapsed_ms: Time taken for retrieval + evaluation.
    """

    query: str
    case_id: Optional[str] = None
    query_type: Optional[str] = None
    collection: Optional[str] = None
    status: str = "success"
    error_type: Optional[str] = None
    error_message: Optional[str] = None
    retrieved_chunk_ids: List[str] = field(default_factory=list)
    generated_answer: Optional[str] = None
    metrics: Dict[str, float] = field(default_factory=dict)
    elapsed_ms: float = 0.0

    def to_dict(self) -> Dict[str, Any]:
        """Serialise a per-query result without losing failure information."""
        return {
            "case_id": self.case_id,
            "query": self.query,
            "query_type": self.query_type,
            "collection": self.collection,
            "status": self.status,
            "error_type": self.error_type,
            "error_message": self.error_message,
            "retrieved_chunk_ids": self.retrieved_chunk_ids,
            "generated_answer": self.generated_answer,
            "metrics": {k: round(v, 4) for k, v in self.metrics.items()},
            "elapsed_ms": round(self.elapsed_ms, 1),
        }


@dataclass
class EvalReport:
    """Aggregated evaluation report across all test cases.

    Attributes:
        query_results: Per-query evaluation results.
        aggregate_metrics: Averaged metrics across all queries.
        total_elapsed_ms: Total time for the entire evaluation.
        evaluator_name: Name of the evaluator used.
        test_set_path: Path to the golden test set file.
    """

    query_results: List[QueryResult] = field(default_factory=list)
    aggregate_metrics: Dict[str, float] = field(default_factory=dict)
    total_elapsed_ms: float = 0.0
    evaluator_name: str = ""
    test_set_path: str = ""
    test_set_sha256: str = ""
    collection: Optional[str] = None
    top_k: int = 10
    run_id: str = ""
    started_at: str = ""
    status_counts: Dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        """Serialise report to dictionary."""
        return {
            "evaluator_name": self.evaluator_name,
            "test_set_path": self.test_set_path,
            "test_set_sha256": self.test_set_sha256,
            "collection": self.collection,
            "top_k": self.top_k,
            "run_id": self.run_id,
            "started_at": self.started_at,
            "total_elapsed_ms": round(self.total_elapsed_ms, 1),
            "aggregate_metrics": {
                k: round(v, 4) for k, v in self.aggregate_metrics.items()
            },
            "query_count": len(self.query_results),
            "status_counts": self.status_counts,
            "query_results": [qr.to_dict() for qr in self.query_results],
        }

    def save_artifacts(self, output_root: str | Path) -> Path:
        """Persist a versioned, non-overwriting evaluation run directory."""
        if not self.run_id:
            raise ValueError("EvalReport.run_id is required before saving artifacts.")

        run_dir = Path(output_root) / self.run_id
        run_dir.mkdir(parents=True, exist_ok=False)

        report_dict = self.to_dict()
        manifest = {
            "schema_version": "1.0",
            "run_id": self.run_id,
            "started_at": self.started_at,
            "evaluator_name": self.evaluator_name,
            "test_set_path": self.test_set_path,
            "test_set_sha256": self.test_set_sha256,
            "collection": self.collection,
            "top_k": self.top_k,
            "query_count": len(self.query_results),
            "status_counts": self.status_counts,
        }
        (run_dir / "manifest.json").write_text(
            json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "aggregate_metrics.json").write_text(
            json.dumps(self.aggregate_metrics, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        (run_dir / "report.json").write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
        query_lines = "".join(
            json.dumps(qr.to_dict(), ensure_ascii=False) + "\n"
            for qr in self.query_results
        )
        (run_dir / "query_results.jsonl").write_text(query_lines, encoding="utf-8")
        return run_dir


def load_test_set(
    path: str | Path,
    *,
    allow_legacy_synthetic: bool = False,
) -> List[GoldenTestCase]:
    """Load golden test set from a JSON file.

    Args:
        path: Path to the golden test set JSON file.

    Returns:
        List of TestCase instances.

    Raises:
        FileNotFoundError: If the file does not exist.
        ValueError: If the file format is invalid.
    """
    file_path = Path(path)
    if not file_path.exists():
        raise FileNotFoundError(f"Golden test set not found: {file_path}")

    with file_path.open("r", encoding="utf-8") as f:
        data = json.load(f)

    if isinstance(data, list):
        if not allow_legacy_synthetic:
            raise ValueError(
                "Legacy list-form golden sets are disabled because they may be "
                "synthetic fixtures. Use a versioned object with a 'test_cases' "
                "key, or opt in with allow_legacy_synthetic=True."
            )
        return [
            GoldenTestCase(
                query=tc.get("question", ""),
                case_id=tc.get("id") or tc.get("query_id"),
                query_type=tc.get("query_type"),
                collection=tc.get("collection"),
                expected_chunk_ids=tc.get("supporting_chunk_ids", []),
                expected_sources=tc.get("expected_sources", []),
                reference_answer=tc.get("answer"),
            )
            for tc in data
            if isinstance(tc, dict)
        ]

    if not isinstance(data, dict) or "test_cases" not in data:
        raise ValueError(
            "Invalid golden test set format: missing 'test_cases' key."
        )

    if not isinstance(data["test_cases"], list):
        raise ValueError("Invalid golden test set format: 'test_cases' must be a list.")

    cases = [GoldenTestCase.from_dict(tc) for tc in data["test_cases"]]
    for index, case in enumerate(cases):
        if not case.query or not case.query.strip():
            raise ValueError(f"Invalid golden test set: test_cases[{index}].query is empty.")
    return cases


class EvalRunner:
    """Runs batch evaluation against a golden test set.

    This class orchestrates:
    1. Loading the golden test set
    2. Running HybridSearch for each query
    3. Optionally generating answers
    4. Invoking the evaluator to score each result
    5. Aggregating metrics into an EvalReport

    Example::

        runner = EvalRunner(
            settings=settings,
            hybrid_search=hybrid_search,
            evaluator=evaluator,
        )
        report = runner.run("tests/fixtures/golden_test_set.json")
        print(report.aggregate_metrics)
    """

    def __init__(
        self,
        settings: Any = None,
        hybrid_search: Any = None,
        evaluator: Optional[BaseEvaluator] = None,
        answer_generator: Any = None,
    ) -> None:
        """Initialize EvalRunner.

        Args:
            settings: Application settings.
            hybrid_search: HybridSearch instance for retrieval.
            evaluator: BaseEvaluator instance for scoring.
            answer_generator: Optional callable(query, chunks) -> str
                for generating real Agent answers. If None, generation metrics
                receive no answer and deterministic retrieval metrics still run.
        """
        self.settings = settings
        self.hybrid_search = hybrid_search
        self.evaluator = evaluator
        self.answer_generator = answer_generator

    def run(
        self,
        test_set_path: str | Path,
        top_k: int = 10,
        collection: Optional[str] = None,
        allow_legacy_synthetic: bool = False,
    ) -> EvalReport:
        """Run evaluation on the golden test set.

        Args:
            test_set_path: Path to golden_test_set.json.
            top_k: Number of chunks to retrieve per query.
            collection: Optional collection name filter.

        Returns:
            EvalReport with per-query and aggregate metrics.

        Raises:
            FileNotFoundError: If test set file doesn't exist.
            ValueError: If evaluator or hybrid_search is not set.
        """
        if self.evaluator is None:
            raise ValueError("EvalRunner requires an evaluator.")
        if top_k <= 0:
            raise ValueError("top_k must be greater than zero.")

        test_cases = load_test_set(
            test_set_path,
            allow_legacy_synthetic=allow_legacy_synthetic,
        )
        if not test_cases:
            raise ValueError("Golden test set is empty.")

        logger.info(
            "Starting evaluation: %d test cases, evaluator=%s",
            len(test_cases),
            type(self.evaluator).__name__,
        )

        started_at = datetime.now(timezone.utc)
        test_set_file = Path(test_set_path)
        report = EvalReport(
            evaluator_name=type(self.evaluator).__name__,
            test_set_path=str(test_set_path),
            test_set_sha256=hashlib.sha256(test_set_file.read_bytes()).hexdigest(),
            collection=collection,
            top_k=top_k,
            run_id=started_at.strftime("%Y%m%dT%H%M%S.%fZ"),
            started_at=started_at.isoformat(),
        )

        t0 = time.monotonic()

        for idx, tc in enumerate(test_cases):
            logger.info("Evaluating [%d/%d]: %s", idx + 1, len(test_cases), tc.query[:60])
            qr = self._evaluate_single(tc, top_k=top_k, collection=collection)
            report.query_results.append(qr)

        report.total_elapsed_ms = (time.monotonic() - t0) * 1000.0
        report.aggregate_metrics = self._aggregate_metrics(report.query_results)
        report.status_counts = self._count_statuses(report.query_results)
        total = len(report.query_results)
        successful = report.status_counts.get("success", 0)
        retrieval_failures = report.status_counts.get("retrieval_error", 0)
        report.aggregate_metrics["evaluation_success_rate"] = successful / total
        report.aggregate_metrics["retrieval_success_rate"] = (
            total - retrieval_failures
        ) / total

        logger.info(
            "Evaluation complete: %d queries, aggregate=%s",
            len(report.query_results),
            report.aggregate_metrics,
        )

        return report

    def _evaluate_single(
        self,
        test_case: GoldenTestCase,
        top_k: int = 10,
        collection: Optional[str] = None,
    ) -> QueryResult:
        """Evaluate a single test case.

        Args:
            test_case: The test case to evaluate.
            top_k: Number of results to retrieve.
            collection: Optional collection filter.

        Returns:
            QueryResult with metrics for this test case.
        """
        t0 = time.monotonic()
        effective_collection = test_case.collection or collection
        qr = QueryResult(
            query=test_case.query,
            case_id=test_case.case_id,
            query_type=test_case.query_type,
            collection=effective_collection,
        )

        # Step 1: Retrieve chunks
        try:
            retrieved_chunks = self._retrieve(
                test_case.query, top_k, effective_collection
            )
        except RetrievalError as exc:
            logger.error("Retrieval failed for '%s': %s", test_case.query[:40], exc)
            qr.status = "retrieval_error"
            qr.error_type = type(exc).__name__
            qr.error_message = str(exc)
            qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
            return qr

        qr.retrieved_chunk_ids = [
            self._get_chunk_id(c) for c in retrieved_chunks
        ]

        # Step 2: Generate answer (if generator available)
        try:
            answer = self._generate_answer(test_case.query, retrieved_chunks)
        except Exception as exc:
            logger.error("Answer generation failed for '%s': %s", test_case.query[:40], exc)
            qr.status = "answer_generation_error"
            qr.error_type = type(exc).__name__
            qr.error_message = str(exc)
            qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
            return qr
        qr.generated_answer = answer

        # Step 3: Build ground truth
        ground_truth = (
            {"ids": test_case.expected_chunk_ids}
            if test_case.expected_chunk_ids
            else None
        )

        # Step 4: Evaluate
        try:
            metrics = self.evaluator.evaluate(  # type: ignore[union-attr]
                query=test_case.query,
                retrieved_chunks=retrieved_chunks,
                generated_answer=answer,
                ground_truth=ground_truth,
                top_k=top_k,
            )
            qr.metrics = metrics
        except Exception as exc:
            logger.warning("Evaluation failed for '%s': %s", test_case.query[:40], exc)
            qr.status = "evaluation_error"
            qr.error_type = type(exc).__name__
            qr.error_message = str(exc)
            qr.metrics = {}

        qr.elapsed_ms = (time.monotonic() - t0) * 1000.0
        return qr

    def _retrieve(
        self,
        query: str,
        top_k: int,
        collection: Optional[str],
    ) -> List[Any]:
        """Retrieve chunks using HybridSearch.

        An empty result list is a valid retrieval outcome. Infrastructure
        failures raise ``RetrievalError`` so they cannot be mistaken for a
        normal zero-recall query.
        """
        if self.hybrid_search is None:
            raise RetrievalError("No HybridSearch instance is configured.")

        try:
            filters = {"collection": collection} if collection else None
            results = self.hybrid_search.search(
                query=query,
                top_k=top_k,
                filters=filters,
            )
            return results if isinstance(results, list) else results.results
        except Exception as exc:
            raise RetrievalError(str(exc)) from exc

    def _generate_answer(self, query: str, chunks: List[Any]) -> Optional[str]:
        """Generate an answer from retrieved chunks.

        Only a real, explicitly supplied answer generator is used. Returning
        ``None`` without one prevents Ragas from accidentally scoring a
        concatenation of retrieved chunks as if it were an Agent answer.
        """
        if self.answer_generator is not None:
            return self.answer_generator(query, chunks)
        return None

    def _get_chunk_id(self, chunk: Any) -> str:
        """Extract chunk ID from various representations."""
        if isinstance(chunk, str):
            return chunk
        if isinstance(chunk, dict):
            for key in ("id", "chunk_id"):
                if key in chunk:
                    return str(chunk[key])
            return str(chunk)
        if hasattr(chunk, "chunk_id"):
            return str(getattr(chunk, "chunk_id"))
        if hasattr(chunk, "id"):
            return str(getattr(chunk, "id"))
        return str(chunk)

    @staticmethod
    def _aggregate_metrics(results: List[QueryResult]) -> Dict[str, float]:
        """Compute average metrics across all query results.

        Args:
            results: List of QueryResult with per-query metrics.

        Returns:
            Dictionary of average metric values.
        """
        if not results:
            return {}

        # Collect all metric keys
        all_keys: set[str] = set()
        for qr in results:
            all_keys.update(qr.metrics.keys())

        # Average each metric over the full query set. Missing metrics count as
        # zero, while QueryResult.status explains whether the zero came from an
        # evaluator/infrastructure failure. Failed cases therefore never vanish
        # from the denominator.
        averages: Dict[str, float] = {}
        for key in sorted(all_keys):
            values = [qr.metrics.get(key, 0.0) for qr in results]
            averages[key] = sum(values) / len(results)

        return averages

    @staticmethod
    def _count_statuses(results: List[QueryResult]) -> Dict[str, int]:
        """Count per-query statuses for operational quality reporting."""
        counts: Dict[str, int] = {}
        for result in results:
            counts[result.status] = counts.get(result.status, 0) + 1
        return dict(sorted(counts.items()))

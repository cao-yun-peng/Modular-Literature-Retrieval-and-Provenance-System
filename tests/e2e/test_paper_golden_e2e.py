import os
import json
import pytest

GOLDEN_PATH = os.path.join(os.path.dirname(__file__), '..', 'fixtures', 'paper_golden_set.json')


class TestPaperGoldenSet:
    """Validate the paper golden QA set structure and evaluator integration."""

    @pytest.fixture(autouse=True)
    def load_golden(self):
        if not os.path.exists(GOLDEN_PATH):
            pytest.skip('paper_golden_set.json not present')
        with open(GOLDEN_PATH, 'r', encoding='utf-8') as f:
            self.golden = json.load(f)
        if not self.golden:
            pytest.skip('golden set empty')

    def test_golden_set_has_required_fields(self):
        """Every entry must have id, question, answer, supporting_chunk_ids."""
        for i, entry in enumerate(self.golden):
            assert isinstance(entry, dict), f"Entry {i} is not a dict"
            assert "id" in entry, f"Entry {i} missing 'id'"
            assert "question" in entry and entry["question"].strip(), \
                f"Entry {i} missing or empty 'question'"
            assert "answer" in entry and entry["answer"].strip(), \
                f"Entry {i} missing or empty 'answer'"
            assert isinstance(entry.get("supporting_chunk_ids"), list), \
                f"Entry {i} 'supporting_chunk_ids' must be a list"

    def test_golden_set_size(self):
        """Golden set should have a reasonable number of entries."""
        assert len(self.golden) >= 20, \
            f"Expected at least 20 entries, got {len(self.golden)}"

    def test_all_ids_unique(self):
        """All entry IDs must be unique."""
        ids = [e["id"] for e in self.golden]
        assert len(ids) == len(set(ids)), "Duplicate IDs found in golden set"

    def test_evaluator_works_with_golden_data(self):
        """CustomEvaluator should compute hit_rate and MRR from golden data."""
        from src.libs.evaluator.custom_evaluator import CustomEvaluator
        evaluator = CustomEvaluator()
        sample = self.golden[0]

        # Simulate: retrieved chunks match the expected ones (perfect hit)
        metrics = evaluator.evaluate(
            query=sample["question"],
            retrieved_chunks=[
                {"chunk_id": cid} for cid in sample["supporting_chunk_ids"]
            ],
            ground_truth={"ids": sample["supporting_chunk_ids"]},
        )
        assert metrics["hit_rate"] == 1.0, "Perfect match should give hit_rate=1.0"
        assert metrics["mrr"] == 1.0, "First-position match should give MRR=1.0"

        # Simulate: no match
        metrics = evaluator.evaluate(
            query=sample["question"],
            retrieved_chunks=[{"chunk_id": "unrelated_chunk"}],
            ground_truth={"ids": sample["supporting_chunk_ids"]},
        )
        assert metrics["hit_rate"] == 0.0, "No match should give hit_rate=0.0"
        assert metrics["mrr"] == 0.0, "No match should give MRR=0.0"

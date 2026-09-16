"""Preserve the prior exhaustive inversion and tie ordering when building faster."""

import random

from src.ingestion.storage.bm25_indexer import BM25Indexer


def test_single_pass_matches_exhaustive_index_and_rankings(tmp_path):
    rng = random.Random(42)
    stats = []
    for i in range(90):
        terms = {str(t): rng.randrange(1, 4) for t in rng.sample(range(200), 12)}
        stats.append(
            {"chunk_id": f"c{i}", "term_frequencies": terms, "doc_length": sum(terms.values())}
        )
    terms = list(dict.fromkeys(t for s in stats for t in s["term_frequencies"]))
    indexer = BM25Indexer(str(tmp_path / "linear"))
    expected = {}
    for term in terms:
        postings = [
            {
                "chunk_id": s["chunk_id"],
                "tf": s["term_frequencies"][term],
                "doc_length": s["doc_length"],
            }
            for s in stats
            if term in s["term_frequencies"]
        ]
        df = len(postings)
        expected[term] = {
            "idf": indexer._calculate_idf(len(stats), df),
            "df": df,
            "postings": postings,
        }
    indexer.build(stats, "fixture")
    assert indexer._index == expected
    assert list(indexer._index) == terms
    reference = BM25Indexer(str(tmp_path / "reference"))
    reference._index, reference._metadata = expected, indexer._metadata.copy()
    for query in (["3"], ["2", "7"], ["missing"], terms[:8]):
        assert indexer.query(query, 20) == reference.query(query, 20)
    before = indexer.query(terms[:8], 20)
    indexer.build(stats, "fixture")
    assert indexer.query(terms[:8], 20) == before

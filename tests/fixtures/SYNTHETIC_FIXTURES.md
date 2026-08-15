# Synthetic evaluation fixtures

`paper_golden_set.json` is a legacy synthetic fixture. Its sequential
`paper_chunk_001`–`paper_chunk_050` identifiers do not refer to the indexed
10-paper corpus and its scores must not be used as retrieval-quality evidence.

The production evaluation loader rejects list-form fixtures by default. The
file may only be loaded explicitly for parser/evaluator smoke tests:

```powershell
.\.venv\Scripts\python.exe scripts\evaluate.py --validate-only `
  --allow-legacy-synthetic `
  --test-set tests\fixtures\paper_golden_set.json
```

Use `golden_test_set.json` or a separately reviewed, object-form test set for
quality reports.

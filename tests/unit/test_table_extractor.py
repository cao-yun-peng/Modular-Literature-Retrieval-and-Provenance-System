from src.ingestion.transform.table_extractor import TableExtractor


def test_table_extraction_roundtrip():
    """extract_tables_from_text should parse markdown tables and return (tables, modified_text)."""
    extractor = TableExtractor()
    markdown = (
        "| Name | Age |\n"
        "|------|-----|\n"
        "| Ali  | 30  |\n"
        "| Bob  | 25  |\n"
    )
    tables, modified_text = extractor.extract_tables_from_text(markdown, source_path="test.md")
    assert isinstance(tables, list)
    assert len(tables) == 1
    assert "rows" in tables[0]
    assert "csv" in tables[0]
    assert "[TABLE:" in modified_text

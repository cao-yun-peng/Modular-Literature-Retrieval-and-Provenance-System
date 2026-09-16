from dataclasses import replace

import pytest

from src.core.settings import load_settings
from src.core.types import Document
from src.ingestion.chunking.document_chunker import DocumentChunker
from src.libs.loader.mineru_agent import markdown_structure
from src.libs.splitter.structured_token import structured_spans, pack_references
from src.libs.splitter.token_splitter import TokenSplitter


def splitter(size=100, overlap=20):
    settings = load_settings()
    return TokenSplitter(replace(settings, ingestion=replace(settings.ingestion,
        chunk_size=size, chunk_overlap=overlap)))


def test_unicode_math_coverage_overlap_and_bound():
    engine = splitter()
    formula = '$$' + 'x_i + ' * 15 + '0$$'
    text = ('中文🙂 paragraph. ' * 25) + '\n\n' + formula + '\n\n' + ('tail words. ' * 100)
    spans = list(structured_spans(engine, text))
    covered = 0
    for start, end, overlap in spans:
        assert start <= covered < end
        assert engine.count(text[start:end]) <= 100
        assert overlap == engine.count(text[start:covered])
        assert text[start:end].count('$$') % 2 == 0
        covered = end
    assert covered == len(text)
    assert any(overlap > 0 for _, _, overlap in spans)


def test_oversized_formula_fails_before_embedding():
    with pytest.raises(ValueError, match='single formula/table'):
        list(structured_spans(splitter(), '$$' + 'x + ' * 200 + '$$'))


def test_reference_entries_remain_whole_without_overlap():
    entries = [f'[{n}] Author title ' + 'words ' * 20 for n in range(1, 12)]
    engine = splitter()
    pieces = list(pack_references(engine, '\n\n'.join(entries)))
    assert '\n\n'.join(pieces) == '\n\n'.join(entries).strip()
    assert all(engine.count(p) <= 100 for p in pieces)


def test_abstract_once_and_caption_bounded():
    abstract = 'This abstract describes an anisotropic system. ' * 40
    text, meta = markdown_structure('# Paper\n\nAuthor\n\n' + abstract +
        '\n\nDOI: example\n\nMechanism—The result.\n\nFIG. 3. ' + 'caption ' * 3000)
    meta.update(title='Paper', paper_mode=True, source_path='fixture.pdf')
    chunks = DocumentChunker(load_settings()).split_document(Document(id='doc_test', text=text, metadata=meta))
    assert all(c.metadata['token_count'] <= 2500 for c in chunks)
    assert sum(abstract.strip() in c.text for c in chunks) == 1
    figures = [c for c in chunks if c.metadata['chunk_type'] == 'figure']
    assert len(figures) > 1
    assert all(c.metadata['figure_id'] == 'fig_3' for c in figures)
    assert [c.metadata['actual_overlap_tokens'] for c in figures][0] == 0


def test_explicit_abstract_with_multiple_paragraphs_removed_from_body():
    _, meta = markdown_structure('# Paper\n\n## Abstract\n\nFirst paragraph.\n\nSecond paragraph.\n\n## Results\n\nResult.')
    assert meta['abstract'] == 'First paragraph.\n\nSecond paragraph.'
    assert meta['abstract_source'] == 'explicit_heading'
    assert all('First paragraph.' not in s['paragraphs'] for s in meta['paper_sections'])

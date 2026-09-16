"""Token-bounded windows that never cut display math or HTML tables.

Offsets refer to normalized section text. Overlap is a target, not a requirement
to repeat an entire large atomic object.
"""
import re


def structured_spans(splitter, text):
    protected = [(m.start(), m.end()) for m in re.finditer(
        r"\$\$[\s\S]*?\$\$|\\\[[\s\S]*?\\\]|<table\b[^>]*>[\s\S]*?</table>",
        text, re.IGNORECASE)]
    for a, b in protected:
        if splitter.count(text[a:b]) > splitter.chunk_size:
            raise ValueError("A single formula/table exceeds the token limit; manual restructuring required")
    start, previous_end = 0, 0
    while start < len(text):
        spans = splitter.split_spans(text[start:])
        if not spans:
            break
        end = start + spans[0][1]
        for a, b in protected:
            if a < end < b:
                end = a if a > start else b
                break
        if end <= previous_end:
            start = previous_end
            continue
        piece = text[start:end]
        if splitter.count(piece) > splitter.chunk_size:
            raise ValueError("Structured chunk exceeds token limit")
        if piece.strip():
            yield start, end, splitter.count(text[start:previous_end]) if start < previous_end else 0
        if end == len(text):
            break
        # Choose a Unicode-safe suffix no larger than the overlap target.
        lo, hi = start + 1, end
        while lo < hi:
            mid = (lo + hi) // 2
            if splitter.count(text[mid:end]) <= splitter.chunk_overlap:
                hi = mid
            else:
                lo = mid + 1
        next_start = lo
        for a, b in protected:
            if a < next_start < b:
                next_start = b
                break
        previous_end, start = end, next_start


def pack_references(splitter, text):
    """Pack complete numbered bibliography entries without overlap."""
    entries = re.split(r"\n\s*\n|\n(?=\[\d+\]\s)", text.strip())
    current = ""
    for entry in entries:
        if not entry.strip():
            continue
        if splitter.count(entry) > splitter.chunk_size:
            raise ValueError("A single reference exceeds the token limit")
        combined = current + "\n\n" + entry if current else entry
        if splitter.count(combined) > splitter.chunk_size:
            yield current
            current = entry
        else:
            current = combined
    if current:
        yield current

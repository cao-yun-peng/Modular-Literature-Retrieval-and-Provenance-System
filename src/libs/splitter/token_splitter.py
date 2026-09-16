"""Token windows on original UTF-8 boundaries; no fixed number of document parts."""

from __future__ import annotations

import bisect
import os
import re

import tiktoken

from src.libs.splitter.base_splitter import BaseSplitter


def get_tokenizer(name="cl100k_base"):
    from src.core.settings import resolve_path

    os.environ.setdefault("TIKTOKEN_CACHE_DIR", str(resolve_path("data/tokenizers")))
    return tiktoken.get_encoding(name)


class TokenSplitter(BaseSplitter):
    def __init__(self, settings, **kwargs):
        self.chunk_size = settings.ingestion.chunk_size
        self.chunk_overlap = settings.ingestion.chunk_overlap
        self.tokenizer_name = settings.ingestion.tokenizer
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ValueError("Require 0 <= token overlap < token chunk size")
        self.encoding = get_tokenizer(self.tokenizer_name)

    def count(self, text):
        return len(self.encoding.encode(text, disallowed_special=()))

    def split_spans(self, text):
        ids = self.encoding.encode(text, disallowed_special=())
        if not text.strip():
            return []
        token_bytes = [0]
        for token in ids:
            token_bytes.append(
                token_bytes[-1] + len(self.encoding.decode_single_token_bytes(token))
            )
        byte_to_char, offset = {0: 0}, 0
        for i, char in enumerate(text):
            offset += len(char.encode("utf-8"))
            byte_to_char[offset] = i + 1
        spans, start = [], 0
        while start < len(ids):
            end = min(start + self.chunk_size, len(ids))
            while end > start and token_bytes[end] not in byte_to_char:
                end -= 1
            if end == start:
                raise ValueError("Token limit is too small to preserve a Unicode character")
            char_start, char_end = byte_to_char[token_bytes[start]], byte_to_char[token_bytes[end]]
            if end < len(ids):
                # Prefer a paragraph/sentence boundary near the end of this window.
                near = byte_to_char.get(token_bytes[start + int((end - start) * 0.85)], char_start)
                candidates = list(re.finditer(r"\n\s*\n|(?<=[.!?。！？])\s+", text[near:char_end]))
                if candidates:
                    preferred = near + candidates[-1].end()
                    preferred_byte = len(text[:preferred].encode("utf-8"))
                    new_end = bisect.bisect_right(token_bytes, preferred_byte) - 1
                    while new_end > start and token_bytes[new_end] not in byte_to_char:
                        new_end -= 1
                    if new_end - start > self.chunk_size * 0.7:
                        end, char_end = new_end, byte_to_char[token_bytes[new_end]]
            # Re-encoding a substring can change boundary merges: enforce the actual bound.
            while self.count(text[char_start:char_end]) > self.chunk_size:
                end -= 1
                while token_bytes[end] not in byte_to_char:
                    end -= 1
                char_end = byte_to_char[token_bytes[end]]
            if text[char_start:char_end].strip():
                spans.append((char_start, char_end))
            if end == len(ids):
                break
            next_start = max(start + 1, end - self.chunk_overlap)
            while token_bytes[next_start] not in byte_to_char:
                next_start += 1
            start = min(next_start, end)
        return spans

    def split_text(self, text, trace=None, **kwargs):
        return [text[a:b] for a, b in self.split_spans(text)]

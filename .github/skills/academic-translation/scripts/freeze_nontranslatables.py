r"""Freeze non-translatable spans (math/code/placeholders) into stable tokens.

This helper is optional but recommended for academic translation workflows.

It scans a UTF-8 text/Markdown/LaTeX file and replaces:
- Display math blocks: $$ ... $$
- LaTeX math blocks: \[ ... \]
- LaTeX environments: \begin{equation|align|gather|cases|...} ... \end{...}
- Inline math: $...$ (best-effort)
- Markdown fenced code blocks: ``` ... ```
- Repo placeholders: [IMAGE: ...]

with tokens like <<MATH:0001>>, <<CODE:0001>>, <<PH:0001>>.

It outputs:
- <input>.frozen<ext>
- <input>.freeze_map.json

Limitations:
- Inline math detection is best-effort and may be confused by dollar amounts.
- Prefer providing LaTeX source (.tex) over PDF for highest accuracy.
"""

from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, List, Tuple


@dataclass(frozen=True)
class FrozenSpan:
    token: str
    kind: str  # MATH | CODE | PH
    text: str


_FENCED_CODE_RE = re.compile(r"(^```.*?$)(.*?)(^```\s*$)", re.MULTILINE | re.DOTALL)
_IMAGE_PLACEHOLDER_RE = re.compile(r"\[IMAGE:\s*[^\]]+\]")

# Display math $$ ... $$ (non-greedy, multiline)
_DOLLAR_BLOCK_RE = re.compile(r"\$\$(.+?)\$\$", re.DOTALL)
# LaTeX \[ ... \]
_BRACKET_BLOCK_RE = re.compile(r"\\\[(.+?)\\\]", re.DOTALL)

# Common LaTeX math environments
_ENV_NAMES = (
    "equation",
    "equation*",
    "align",
    "align*",
    "gather",
    "gather*",
    "multline",
    "multline*",
    "split",
    "cases",
)
_ENV_BLOCK_RE = re.compile(
    r"\\begin\{(" + "|".join(re.escape(n) for n in _ENV_NAMES) + r")\}(.*?)\\end\{\1\}",
    re.DOTALL,
)


def _iter_inline_math_spans(text: str) -> Iterable[Tuple[int, int]]:
    r"""Best-effort inline $...$ span detection.

    Rules:
    - Ignore escaped dollars (\$)
    - Do not match $$ (handled by block regex)
    - Do not match across newlines
    """

    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\\":
            i += 2
            continue
        if ch != "$":
            i += 1
            continue

        # Skip $$
        if i + 1 < n and text[i + 1] == "$":
            i += 2
            continue

        start = i
        i += 1
        while i < n:
            if text[i] == "\n":
                break
            if text[i] == "\\":
                i += 2
                continue
            if text[i] == "$":
                # Skip $$ as closer too
                if i + 1 < n and text[i + 1] == "$":
                    i += 2
                    continue
                end = i + 1
                yield (start, end)
                break
            i += 1
        else:
            break

        # Continue scanning after end
        i = end


def _replace_spans(text: str, spans: List[Tuple[int, int]], make_token) -> Tuple[str, List[FrozenSpan]]:
    """Replace spans (start,end) in reverse order."""
    frozen: List[FrozenSpan] = []
    for start, end in sorted(spans, key=lambda s: s[0], reverse=True):
        raw = text[start:end]
        token, kind = make_token(raw)
        frozen.append(FrozenSpan(token=token, kind=kind, text=raw))
        text = text[:start] + token + text[end:]
    frozen.reverse()
    return text, frozen


def freeze_text(text: str) -> Tuple[str, List[FrozenSpan]]:
    """Freeze code blocks, placeholders, math blocks, then inline math."""

    frozen_all: List[FrozenSpan] = []
    counters = {"MATH": 0, "CODE": 0, "PH": 0}

    def next_token(kind: str) -> str:
        counters[kind] += 1
        return f"<<{kind}:{counters[kind]:04d}>>"

    # 1) Fenced code blocks
    code_spans: List[Tuple[int, int]] = []
    for m in _FENCED_CODE_RE.finditer(text):
        code_spans.append((m.start(), m.end()))

    def code_token(_raw: str):
        return next_token("CODE"), "CODE"

    text, frozen = _replace_spans(text, code_spans, code_token)
    frozen_all.extend(frozen)

    # 2) Image placeholders
    ph_spans: List[Tuple[int, int]] = []
    for m in _IMAGE_PLACEHOLDER_RE.finditer(text):
        ph_spans.append((m.start(), m.end()))

    def ph_token(_raw: str):
        return next_token("PH"), "PH"

    text, frozen = _replace_spans(text, ph_spans, ph_token)
    frozen_all.extend(frozen)

    # 3) LaTeX environments
    env_spans: List[Tuple[int, int]] = []
    for m in _ENV_BLOCK_RE.finditer(text):
        env_spans.append((m.start(), m.end()))

    def math_token(_raw: str):
        return next_token("MATH"), "MATH"

    text, frozen = _replace_spans(text, env_spans, math_token)
    frozen_all.extend(frozen)

    # 4) Display math $$...$$ and \[...\]
    block_spans: List[Tuple[int, int]] = []
    for rx in (_DOLLAR_BLOCK_RE, _BRACKET_BLOCK_RE):
        for m in rx.finditer(text):
            block_spans.append((m.start(), m.end()))

    text, frozen = _replace_spans(text, block_spans, math_token)
    frozen_all.extend(frozen)

    # 5) Inline $...$ (best-effort)
    inline_spans = list(_iter_inline_math_spans(text))
    text, frozen = _replace_spans(text, inline_spans, math_token)
    frozen_all.extend(frozen)

    return text, frozen_all


def main() -> None:
    parser = argparse.ArgumentParser(description="Freeze non-translatable spans into tokens")
    parser.add_argument("input", type=Path, help="Input UTF-8 text/markdown/latex file")
    args = parser.parse_args()

    in_path: Path = args.input
    if not in_path.exists():
        raise SystemExit(f"File not found: {in_path}")

    text = in_path.read_text(encoding="utf-8")
    frozen_text, frozen_spans = freeze_text(text)

    out_path = in_path.with_name(in_path.stem + ".frozen" + in_path.suffix)
    map_path = in_path.with_name(in_path.name + ".freeze_map.json")

    out_path.write_text(frozen_text, encoding="utf-8")
    map_path.write_text(
        json.dumps([asdict(s) for s in frozen_spans], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Wrote: {out_path}")
    print(f"Wrote: {map_path}")
    print(f"Frozen spans: {len(frozen_spans)}")


if __name__ == "__main__":
    main()

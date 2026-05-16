"""Rehydrate tokens produced by freeze_nontranslatables.py.

Input:
- translated file that still contains tokens (e.g., xxx.frozen.en.md)
- the JSON map produced by freeze_nontranslatables.py

Output:
- a file with tokens replaced back to the original spans

Safety checks:
- Ensures each token exists exactly once in the translated text.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List


def main() -> None:
    parser = argparse.ArgumentParser(description="Rehydrate non-translatable tokens back to original spans")
    parser.add_argument("translated", type=Path, help="Translated file containing tokens")
    parser.add_argument("map", type=Path, help="freeze_map.json path")
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output file path (default: <translated>.rehydrated<ext>)",
    )
    args = parser.parse_args()

    translated_path: Path = args.translated
    map_path: Path = args.map

    if not translated_path.exists():
        raise SystemExit(f"File not found: {translated_path}")
    if not map_path.exists():
        raise SystemExit(f"File not found: {map_path}")

    text = translated_path.read_text(encoding="utf-8")
    spans: List[Dict[str, str]] = json.loads(map_path.read_text(encoding="utf-8"))

    # Validate tokens
    for item in spans:
        token = item["token"]
        count = text.count(token)
        if count != 1:
            raise SystemExit(
                f"Token occurrence check failed for {token}: expected 1, got {count}. "
                "Do not add/remove/rename tokens during translation."
            )

    # Replace
    for item in spans:
        text = text.replace(item["token"], item["text"])

    out_path = args.output
    if out_path is None:
        out_path = translated_path.with_name(translated_path.stem + ".rehydrated" + translated_path.suffix)

    out_path.write_text(text, encoding="utf-8")
    print(f"Wrote: {out_path}")


if __name__ == "__main__":
    main()

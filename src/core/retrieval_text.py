"""Augment retrieval input while preserving the original source text in storage."""

import json


def image_annotations(metadata):
    def mapping(key):
        value = metadata.get(key) or {}
        if isinstance(value, str):
            value = json.loads(value)
        return value if isinstance(value, dict) else {}

    if not metadata.get("captions_separate"):
        return []
    records = mapping("image_caption_records")
    return [
        {
            "image_id": image_id,
            "description": caption,
            **records.get(image_id, {}),
            "is_source_text": False,
        }
        for image_id, caption in mapping("image_captions").items()
    ]


def retrieval_text(chunk):
    if not chunk.metadata.get("captions_separate"):
        return chunk.text
    captions = chunk.metadata.get("image_captions") or {}
    if isinstance(captions, str):
        captions = json.loads(captions)
    if not captions:
        return chunk.text
    from src.libs.splitter.token_splitter import get_tokenizer

    encoding = get_tokenizer(chunk.metadata.get("tokenizer", "cl100k_base"))
    prefix = "[AI-generated image descriptions; unreviewed]\n"
    prefix_size = len(encoding.encode(prefix, disallowed_special=()))
    per_image = max(1, (1000 - prefix_size - 2 * len(captions)) // len(captions))
    parts = []
    for _, caption in sorted(captions.items()):
        ids = encoding.encode(str(caption), disallowed_special=())[:per_image]
        text = b"".join(encoding.decode_single_token_bytes(t) for t in ids).decode(
            "utf-8", errors="ignore"
        )
        parts.append(text)
    # Include all formatting in the supplement budget; full notes remain in metadata.
    supplement = encoding.encode(prefix + "\n".join(parts), disallowed_special=())[:1000]
    supplement_text = b"".join(encoding.decode_single_token_bytes(t) for t in supplement).decode(
        "utf-8", errors="ignore"
    )
    return chunk.text + "\n\n" + supplement_text

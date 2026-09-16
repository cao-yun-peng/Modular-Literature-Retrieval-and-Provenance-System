"""Qwen-VL descriptions with persistent content-addressed cache."""

from __future__ import annotations

import base64
import io
import json
import uuid
from pathlib import Path

from PIL import Image

from src.libs.dashscope_client import DashScopeClient, DashScopeError, sha
from src.libs.llm.base_llm import ChatResponse
from src.libs.llm.base_vision_llm import BaseVisionLLM


class DashScopeVisionLLM(BaseVisionLLM):
    def __init__(self, settings, **kwargs):
        self.model = settings.vision_llm.model
        self.max_size = settings.vision_llm.max_image_size
        self.max_output_tokens = settings.vision_llm.max_output_tokens
        self.client = DashScopeClient(settings, settings.vision_llm)
        self.cache = self.client.root / "vision_cache"
        self.cache.mkdir(exist_ok=True)

    def chat_with_image(self, text, image, messages=None, trace=None, **kwargs):
        self.validate_text(text)
        self.validate_image(image)
        raw = Path(image.path).read_bytes() if image.path else image.data
        if raw is None:
            raw = base64.b64decode(image.base64)
        with Image.open(io.BytesIO(raw)) as original:
            prepared = original.convert("RGB")
            prepared.thumbnail((self.max_size, self.max_size))
            buffer = io.BytesIO()
            prepared.save(buffer, format="PNG")
        image_bytes = buffer.getvalue()
        history = [{"role": m.role, "content": m.content} for m in (messages or [])]
        options = {"max_tokens": kwargs.get("max_tokens", self.max_output_tokens), "temperature": 0}
        identity = {"kind": "vision", "image_sha256": sha(raw), "prompt_sha256": sha(text)}
        cache_id = sha(
            json.dumps(
                {
                    **identity,
                    "image_payload_sha256": sha(image_bytes),
                    "model": self.model,
                    "endpoint": self.client.base_url,
                    "history": history,
                    **options,
                },
                sort_keys=True,
            )
        )
        path = self.cache / (cache_id + ".json")
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            self.client.record(
                {
                    **identity,
                    "model": self.model,
                    "cache_hit": True,
                    "success": True,
                    "usage": {"total_tokens": 0},
                    "cache_id": cache_id,
                }
            )
            return ChatResponse(
                data["content"],
                self.model,
                {"total_tokens": 0},
                {**data, "cache_hit": True, "cache_id": cache_id},
            )
        payload = {
            "model": self.model,
            **options,
            "messages": history
            + [
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": "data:image/png;base64,"
                                + base64.b64encode(image_bytes).decode()
                            },
                        },
                        {"type": "text", "text": text},
                    ],
                }
            ],
        }
        response = self.client.post("/chat/completions", payload, identity, trace)
        choice = response["choices"][0]
        content = choice["message"].get("content")
        if not content or choice.get("finish_reason") != "stop":
            raise DashScopeError("Vision description is empty or incomplete; not cached")
        record = {
            **identity,
            "content": content,
            "model": self.model,
            "usage": response.get("usage"),
            "request_id": response.get("id"),
            "status": "model_generated_unreviewed",
            "cache_hit": False,
        }
        temporary = path.with_suffix("." + uuid.uuid4().hex + ".tmp")
        temporary.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        temporary.replace(path)
        return ChatResponse(
            content, self.model, response.get("usage"), {**record, "cache_id": cache_id}
        )

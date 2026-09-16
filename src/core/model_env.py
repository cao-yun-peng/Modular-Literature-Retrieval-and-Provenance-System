"""Load supported project model variables without logging credentials."""

import os
from pathlib import Path


def load_model_env() -> None:
    from dotenv import dotenv_values

    values = {}
    root = Path(__file__).resolve().parents[2]
    for name in (".env", ".env.local"):
        if (root / name).exists():
            values.update(dotenv_values(root / name))
    for key, value in values.items():
        if value and (key.startswith(("DASHSCOPE_", "QWEN_")) or key == "DEEPSEEK_API_KEY"):
            os.environ.setdefault(key, value)

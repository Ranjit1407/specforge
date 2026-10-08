import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

def _models() -> list[str]:
    primary = os.environ.get("PRIMARY_MODEL", "google/gemma-4-31b-it:free")
    fallbacks = os.environ.get("FALLBACK_MODEL", "google/gemma-4-26b-a4b-it:free").split(",")
    return list(dict.fromkeys(m.strip() for m in [primary, *fallbacks] if m.strip()))


@dataclass
class Settings:
    api_key: str = field(default_factory=lambda: os.environ.get("OPENROUTER_API_KEY", ""))
    models: list[str] = field(default_factory=_models)
    base_url: str = "https://openrouter.ai/api/v1"
    request_timeout: float = 300.0
    max_attempts: int = 8
    cache_dir: Path = Path(__file__).resolve().parent.parent / ".cache"
    use_cache: bool = True
    use_dense: bool = True
    chunk_words: int = 220
    chunk_overlap: int = 40
    top_k: int = 6
    max_context_chunks: int = 16
    # Documents up to this size are read in full by the Scope Analyst (~80K tokens); larger sets are digested in parts.
    full_context_words: int = 60000
    sweep_batch_words: int = 8000

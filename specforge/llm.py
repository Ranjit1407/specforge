import logging
import hashlib
import json
import re
import threading
import time
from pathlib import Path
from typing import TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from .config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)

REPAIR_PROMPT = """Your previous reply could not be parsed or did not match the required JSON shape.

Error:
{error}

Previous reply:
{raw}

Return the corrected JSON only, with no commentary and no code fences. Keep all the content; fix only the structure."""


class LLMError(RuntimeError):
    pass


class Cancelled(Exception):
    """Raised when the caller asked the run to stop."""


def extract_json(text: str):
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, re.S)
    if fenced:
        text = fenced.group(1)
    starts = [i for i in (text.find("{"), text.find("[")) if i != -1]
    if not starts:
        raise ValueError("no JSON object found in reply")
    candidate = text[min(starts): max(text.rfind("}"), text.rfind("]")) + 1]
    try:
        return json.loads(candidate)
    except json.JSONDecodeError:
        return json.loads(re.sub(r",\s*([}\]])", r"\1", candidate))


class LLM:
    """OpenAI-compatible chat client with model fallback, backoff, JSON repair and an on-disk response cache."""

    def __init__(self, settings: Settings, cancel: threading.Event | None = None):
        if not settings.api_key:
            raise LLMError("OPENROUTER_API_KEY is not set; copy .env.example to .env and fill it in")
        self.client = OpenAI(base_url=settings.base_url, api_key=settings.api_key,
                             timeout=settings.request_timeout, max_retries=0,
                             default_headers={"X-Title": "SpecForge"})
        self.models = settings.models
        self.max_attempts = settings.max_attempts
        self.cache: Path | None = settings.cache_dir / "llm" if settings.use_cache else None
        self.cancel = cancel
        self.calls = 0

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def _request(self, **kwargs):
        """Sends one request; with a cancel event, gives up waiting the moment the event is set."""
        if self.cancel is None:
            return self.client.chat.completions.create(**kwargs)
        box: dict = {}

        def run() -> None:
            try:
                box["resp"] = self.client.chat.completions.create(**kwargs)
            except Exception as e:
                box["error"] = e

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.25)
            self._check_cancel()
        if "error" in box:
            raise box["error"]
        return box["resp"]

    def _cache_path(self, kind: str, max_tokens: int, prompt: str) -> Path | None:
        if not self.cache:
            return None
        return self.cache / (hashlib.sha256(f"{kind}\n{max_tokens}\n{prompt}".encode()).hexdigest() + ".cache")

    def complete(self, prompt: str, max_tokens: int = 16000) -> str:
        cached = self._cache_path("text", max_tokens, prompt)
        if cached and cached.exists():
            return cached.read_text(encoding="utf-8")
        text = self._call(prompt, max_tokens)
        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(text, encoding="utf-8")
        return text

    def complete_json(self, prompt: str, schema: type[T], max_tokens: int = 16000) -> T:
        cached = self._cache_path(schema.__name__, max_tokens, prompt)
        if cached and cached.exists():
            return schema.model_validate_json(cached.read_text(encoding="utf-8"))

        raw = self._call(prompt, max_tokens)
        for attempt in range(3):
            try:
                result = schema.model_validate(extract_json(raw))
                break
            except (ValueError, ValidationError) as e:
                if attempt == 2:
                    raise LLMError(f"model did not return valid {schema.__name__} JSON: {e}") from e
                log.warning(f"Reply was not valid {schema.__name__} JSON; asking the model to repair it")
                raw = self._call(REPAIR_PROMPT.format(error=str(e)[:2000], raw=raw[-20000:]), max_tokens)

        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    def _call(self, prompt: str, max_tokens: int) -> str:
        delay, last_error = 15.0, None
        for attempt in range(1, self.max_attempts + 1):
            for model in self.models:
                self._check_cancel()
                started = time.time()
                try:
                    resp = self._request(
                        model=model,
                        messages=[{"role": "user", "content": prompt}],
                        max_tokens=max_tokens,
                        temperature=0.2,
                    )
                except openai.RateLimitError as e:
                    last_error = e
                    log.warning(f"{model}: rate limited")
                    continue
                except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
                    last_error = e
                    log.warning(f"{model}: {type(e).__name__}")
                    continue
                except openai.APIStatusError as e:
                    if e.status_code in (401, 402, 403):
                        raise LLMError(f"The model provider rejected the request ({e.status_code}): {e.message}") from e
                    last_error = e
                    log.warning(f"{model}: HTTP {e.status_code} {e.message[:200]}")
                    continue

                choice = resp.choices[0] if resp.choices else None
                text = (choice.message.content or "").strip() if choice else ""
                if not text:
                    last_error = LLMError(f"{model} returned an empty reply")
                    log.warning(f"{model}: empty reply")
                    continue
                if choice.finish_reason == "length":
                    log.warning(f"{model}: reply hit max_tokens and may be truncated")
                self.calls += 1
                log.info(f"{model} answered in {time.time() - started:.0f}s")
                return text

            if attempt < self.max_attempts:
                log.warning(f"All models failed (attempt {attempt}/{self.max_attempts}); retrying in {delay:.0f}s")
                if self.cancel is not None:
                    if self.cancel.wait(delay):
                        raise Cancelled()
                else:
                    time.sleep(delay)
                delay = min(delay * 2, 120)
        raise LLMError(f"all models failed after {self.max_attempts} attempts: {last_error}")

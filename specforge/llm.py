import hashlib
import json
import logging
import math
import re
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Callable, TypeVar

import openai
from openai import OpenAI
from pydantic import BaseModel, ValidationError

from . import prompts
from .config import Settings

log = logging.getLogger(__name__)
T = TypeVar("T", bound=BaseModel)


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


def _quota_exhausted(error: openai.APIStatusError) -> bool:
    """OpenRouter's daily free-model limit or an empty credit balance; retrying today will not help."""
    text = f"{error.message} {error.body}".lower()
    return error.status_code == 402 or (error.status_code == 429 and ("per-day" in text or "per day" in text))


class LLM:
    """Chat client: OpenRouter models first, local Ollama models as the fallback, with backoff,
    JSON repair and an on-disk response cache."""

    def __init__(self, settings: Settings, cancel: threading.Event | None = None):
        self.local_models = settings.ollama_models
        if not settings.api_key and not self.local_models:
            raise LLMError("OPENROUTER_API_KEY is not set; copy .env.example to .env and fill it in "
                           "(or set OLLAMA_MODEL to run on local models only)")
        self.client = OpenAI(base_url=settings.base_url, api_key=settings.api_key, timeout=settings.request_timeout,
                             max_retries=0, default_headers={"X-Title": "SpecForge"}) if settings.api_key else None
        self.models = settings.models if settings.api_key else []
        self.remote_available = bool(self.models)
        self.ollama_url = settings.ollama_url
        self.ollama_num_ctx = settings.ollama_num_ctx
        self.ollama_max_tokens = settings.ollama_max_tokens
        self.ollama_timeout = settings.ollama_timeout
        self.max_attempts = settings.max_attempts
        self.cache: Path | None = settings.cache_dir / "llm" if settings.use_cache else None
        self.cancel = cancel
        self.calls = 0

    def _check_cancel(self) -> None:
        if self.cancel is not None and self.cancel.is_set():
            raise Cancelled()

    def _cancellable(self, request: Callable[[], object]):
        """Runs one request; with a cancel event, gives up waiting the moment the event is set."""
        if self.cancel is None:
            return request()
        box: dict = {}

        def run() -> None:
            try:
                box["result"] = request()
            except Exception as e:
                box["error"] = e

        worker = threading.Thread(target=run, daemon=True)
        worker.start()
        while worker.is_alive():
            worker.join(0.25)
            self._check_cancel()
        if "error" in box:
            raise box["error"]
        return box["result"]

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

        raw = self._call(prompt, max_tokens, json_mode=True)
        for attempt in range(3):
            try:
                result = schema.model_validate(extract_json(raw))
                break
            except (ValueError, ValidationError) as e:
                if attempt == 2:
                    raise LLMError(f"model did not return valid {schema.__name__} JSON: {e}") from e
                log.warning(f"Reply was not valid {schema.__name__} JSON; asking the model to repair it")
                raw = self._call(prompts.render("json_repair", error=str(e)[:2000], raw=raw[-20000:]), max_tokens,
                                 json_mode=True)

        if cached:
            cached.parent.mkdir(parents=True, exist_ok=True)
            cached.write_text(result.model_dump_json(indent=2), encoding="utf-8")
        return result

    def _call(self, prompt: str, max_tokens: int, json_mode: bool = False) -> str:
        delay, last_error = 15.0, None
        for attempt in range(1, self.max_attempts + 1):
            text, error = self._call_remote(prompt, max_tokens)
            last_error = error or last_error
            if text is None and self.local_models:
                text, error = self._call_local(prompt, max_tokens, json_mode)
                last_error = error or last_error
            if text is not None:
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

    def _call_remote(self, prompt: str, max_tokens: int) -> tuple[str | None, Exception | None]:
        """Tries each OpenRouter model once. Returns the reply, or None and the last error."""
        last_error = None
        for model in self.models if self.remote_available else []:
            self._check_cancel()
            started = time.time()
            try:
                resp = self._cancellable(lambda: self.client.chat.completions.create(
                    model=model, messages=[{"role": "user", "content": prompt}], max_tokens=max_tokens,
                    temperature=0.2))
            except openai.RateLimitError as e:
                last_error = e
                if self.local_models and _quota_exhausted(e):
                    return self._stop_remote("OpenRouter's daily free-model limit is used up"), e
                log.warning(f"{model}: rate limited")
                continue
            except (openai.APITimeoutError, openai.APIConnectionError, openai.InternalServerError) as e:
                last_error = e
                log.warning(f"{model}: {type(e).__name__}")
                continue
            except openai.APIStatusError as e:
                if e.status_code in (401, 402, 403):
                    if not self.local_models:
                        raise LLMError(f"The model provider rejected the request ({e.status_code}): {e.message}") from e
                    reason = "OpenRouter has no credit left" if e.status_code == 402 else \
                        f"OpenRouter rejected the API key ({e.status_code})"
                    return self._stop_remote(reason), e
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
            return text, None
        return None, last_error

    def _stop_remote(self, reason: str) -> None:
        self.remote_available = False
        log.warning(f"{reason}; using local Ollama models ({', '.join(self.local_models)}) for the rest of this run")
        return None

    def _call_local(self, prompt: str, max_tokens: int, json_mode: bool) -> tuple[str | None, Exception | None]:
        """Tries each local Ollama model once, through Ollama's native API so the context size can be set."""
        num_predict = min(max_tokens, self.ollama_max_tokens)
        needed = len(prompt) // 3 + num_predict + 512  # about 3 characters per token, plus room for the reply
        if needed > self.ollama_num_ctx:
            # Ollama would silently cut the start of the prompt (the instructions), so do not send it.
            error = LLMError(f"prompt needs about {needed} tokens of context; OLLAMA_NUM_CTX is {self.ollama_num_ctx}")
            log.warning(f"Skipping local models: {error}")
            return None, error
        num_ctx = min(self.ollama_num_ctx, max(8192, math.ceil(needed / 2048) * 2048))
        last_error = None
        for model in self.local_models:
            self._check_cancel()
            body = {"model": model, "messages": [{"role": "user", "content": prompt}], "stream": True,
                    "options": {"num_ctx": num_ctx, "num_predict": num_predict, "temperature": 0.2}}
            if json_mode:
                body["format"] = "json"
            request = urllib.request.Request(f"{self.ollama_url}/api/chat", data=json.dumps(body).encode(),
                                             headers={"Content-Type": "application/json"})
            started = time.time()
            try:
                data = self._cancellable(lambda: self._stream_ollama(request))
            except urllib.error.HTTPError as e:
                detail = e.read().decode(errors="replace")[:200]
                hint = f"; run `ollama pull {model}`" if e.code == 404 else ""
                last_error = LLMError(f"ollama:{model} HTTP {e.code} {detail}{hint}")
                log.warning(str(last_error))
                continue
            except (LLMError, json.JSONDecodeError) as e:
                last_error = LLMError(f"ollama:{model}: {e}")
                log.warning(str(last_error))
                continue
            except (urllib.error.URLError, TimeoutError, OSError) as e:
                last_error = LLMError(f"Ollama is not reachable at {self.ollama_url} ({getattr(e, 'reason', e)}); "
                                      "is it running?")
                log.warning(str(last_error))
                break
            text = re.sub(r"<think>.*?</think>", "", data["content"], flags=re.S).strip()
            if not text:
                last_error = LLMError(f"ollama:{model} returned an empty reply")
                log.warning(str(last_error))
                continue
            if data.get("done_reason") == "length":
                log.warning(f"ollama:{model}: reply hit num_predict and may be truncated")
            self.calls += 1
            log.info(f"ollama:{model} answered in {time.time() - started:.0f}s")
            return text, None
        return None, last_error

    def _stream_ollama(self, request: urllib.request.Request) -> dict:
        """Reads a streamed reply. Leaving the loop closes the connection, which makes Ollama stop generating,
        so a cancelled run does not keep the local model busy."""
        parts: list[str] = []
        done_reason = None
        with urllib.request.urlopen(request, timeout=self.ollama_timeout) as resp:
            for line in resp:
                if self.cancel is not None and self.cancel.is_set():
                    break
                if not line.strip():
                    continue
                chunk = json.loads(line)
                if "error" in chunk:
                    raise LLMError(f"Ollama error: {chunk['error']}")
                parts.append((chunk.get("message") or {}).get("content") or "")
                if chunk.get("done"):
                    done_reason = chunk.get("done_reason")
                    break
        return {"content": "".join(parts), "done_reason": done_reason}

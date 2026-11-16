"""Google Gemini API client for the curriculum selector (CURRICULUM_LLM_PROVIDER=gemini, the default).

Uses the Gemini Interactions API over plain httpx (no SDK):
POST https://generativelanguage.googleapis.com/<GEMINI_API_VERSION>/interactions

- The key (GEMINI_API_KEY) travels only in the `x-goog-api-key` header: never in the URL, logs,
  errors, public metadata or audit entries.
- The request carries the same SELECTOR_SYSTEM_PROMPT and selector_payload as the other providers
  (no VC data, no user identity, no job documents, no URLs), asks for JSON constrained by a schema
  derived from CurriculumDraft, and sets `store: false` so the interaction is not kept by the API.
- temperature/top_p/top_k are never sent (ignored by current models, rejected by future ones).
- The model reply stays untrusted: curriculum_generator parses and validates it and may discard it.
- No redirects, no proxy environment variables, one bounded call: 20 s per read (3 s connect),
  25 s overall, at most 256 KiB of response body.
- Concurrency across requests is capped by llm_client.llm_slot (CURRICULUM_LLM_MAX_CONCURRENCY).

Failure mapping (ProviderFailure.code, retryable):
  HTTP 408/429/500/502/503/504              -> LLM_PROVIDER_REJECTED, retryable
  any other non-200 (400 incl. safety blocks, 401, 402, 403, 404, 3xx ...)
                                            -> LLM_PROVIDER_REJECTED, not retryable
  connection errors / timeouts              -> LLM_PROVIDER_UNAVAILABLE, retryable
  status failed / cancelled                 -> LLM_PROVIDER_REJECTED, not retryable
  status incomplete / requires_continuation -> reply with complete=False (generator retries once, then falls back)
  anything else unexpected                  -> LLM_MALFORMED_RESPONSE
  body over 256 KiB                         -> LLM_RESPONSE_TOO_LARGE
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

import httpx

from src.career.contracts.curriculum import PROMPT_VERSION
from src.career.errors import ConfigurationError
from src.career.llm_client import (
    MODEL_ID_PATTERN,
    SELECTOR_SYSTEM_PROMPT,
    LLMReply,
    ProviderFailure,
    selector_payload,
)

logger = logging.getLogger(__name__)

PROVIDER = "gemini"
# Fixed host. Tests inject an httpx transport instead of overriding the endpoint.
GEMINI_API_BASE = "https://generativelanguage.googleapis.com"
# v1 is the stable Interactions endpoint; v1beta is the form the guides show. Body and response are identical.
GEMINI_API_VERSIONS = ("v1", "v1beta")
DEFAULT_API_VERSION = "v1"
# Stable, cheapest/fastest 3.x model; default thinking level "minimal"; meant for structured JSON extraction.
DEFAULT_MODEL = "gemini-3.5-flash-lite"
THINKING_LEVELS = ("minimal", "low", "medium", "high")
# Models documented without the "minimal" thinking level (gemini-3.8-flash, Gemini 4).
NO_MINIMAL_THINKING_PREFIXES = ("gemini-3.8-flash", "gemini-4")
MAX_OUTPUT_TOKENS = 4096  # Includes thinking tokens.
REQUEST_TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 262144
MAX_ERROR_BODY_BYTES = 16384
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504})
# Provider error codes are logged only when they look like an identifier (never the message).
SAFE_PROVIDER_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")

# Response schema derived from CurriculumDraft using only keywords the structured-output docs list
# (type, properties, required, additionalProperties, items, maxItems, minimum, maximum).
# The raw pydantic schema adds `format: uuid`, `default` and `$defs`/`$ref`; it still goes to the
# model inside the prompt payload (PROMPT_VERSION unchanged). output_validator and pydantic stay
# authoritative for UUIDs, coverage, order, resource IDs and budgets.
# tests/career/test_gemini_client.py keeps this in step with CurriculumDraft.
DRAFT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "maxItems": 48,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["step_id", "hours"],
                "properties": {
                    "step_id": {"type": "string"},
                    "resource_version_id": {"type": ["string", "null"]},
                    "hours": {"type": "integer", "minimum": 1, "maximum": 40},
                },
            },
        }
    },
}

USAGE_NAMES = {
    "total_input_tokens": "prompt_tokens",
    "total_output_tokens": "completion_tokens",
    "total_thought_tokens": "thought_tokens",
    "total_tokens": "total_tokens",
}


def _valid_key(key: str) -> bool:
    # The key goes into an HTTP header: printable ASCII without whitespace. No prefix is enforced
    # (AI Studio auth keys and older keys look different).
    return bool(key) and key.isascii() and key.isprintable() and not any(ch.isspace() for ch in key)


@dataclass
class GeminiConfig:
    model: str
    thinking_level: str | None
    api_version: str
    api_key: str = field(repr=False)

    provider = PROVIDER

    @classmethod
    def from_env(cls):
        """Read the Gemini settings. Messages name variables and rules only, never values."""
        if os.getenv("CURRICULUM_LLM_ENABLED", "false").lower() != "true":
            raise ConfigurationError("CURRICULUM_LLM_ENABLED=true is required for the curriculum LLM")
        key = os.getenv("GEMINI_API_KEY", "")
        if not _valid_key(key):
            raise ConfigurationError(
                "GEMINI_API_KEY is required (printable ASCII without spaces or line breaks)"
            )
        model = os.getenv("CURRICULUM_LLM_MODEL", "").strip() or DEFAULT_MODEL
        if not MODEL_ID_PATTERN.fullmatch(model):
            raise ConfigurationError(
                "CURRICULUM_LLM_MODEL must be a model ID such as " + DEFAULT_MODEL
            )
        level = os.getenv("CURRICULUM_LLM_THINKING_LEVEL", "").strip().lower() or None
        if level is not None and level not in THINKING_LEVELS:
            raise ConfigurationError(
                "CURRICULUM_LLM_THINKING_LEVEL must be one of: minimal, low, medium, high "
                "(empty means the model default)"
            )
        if level == "minimal" and model.startswith(NO_MINIMAL_THINKING_PREFIXES):
            raise ConfigurationError(
                "CURRICULUM_LLM_THINKING_LEVEL=minimal is not supported by gemini-3.8-flash or "
                "Gemini 4 models (use low or leave it empty)"
            )
        version = os.getenv("GEMINI_API_VERSION", "").strip() or DEFAULT_API_VERSION
        if version not in GEMINI_API_VERSIONS:
            raise ConfigurationError("GEMINI_API_VERSION must be v1 or v1beta (empty means v1)")
        return cls(model, level, version, key)

    @property
    def endpoint(self) -> str:
        return f"{GEMINI_API_BASE}/{self.api_version}/interactions"

    def public_metadata(self):
        # Frozen into the job by BE (GenerationInput.generator) and compared before generating.
        return {
            "provider": PROVIDER,
            "api": "interactions",
            "api_version": self.api_version,
            "model": self.model,
            "thinking_level": self.thinking_level,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "prompt_version": PROMPT_VERSION,
        }

    def create_client(self):
        return GeminiClient(self)


def provider_error_code(data: bytes) -> str | None:
    """Identifier-like error code from an error body (Interactions or classic Google shape), else None.

    Only `error.code` (string), `error.status` and the first `error.details[].reason` are considered,
    each only when it looks like an identifier. `error.message` is never read.
    """
    try:
        payload = json.loads(data)
    except (ValueError, RecursionError):
        return None
    error = payload.get("error") if isinstance(payload, dict) else None
    if not isinstance(error, dict):
        return None
    parts = []
    for name in ("code", "status"):
        value = error.get(name)
        if isinstance(value, str) and SAFE_PROVIDER_CODE.fullmatch(value):
            parts.append(value)
            break
    details = error.get("details")
    if isinstance(details, list):
        for detail in details[:10]:
            reason = detail.get("reason") if isinstance(detail, dict) else None
            if isinstance(reason, str) and SAFE_PROVIDER_CODE.fullmatch(reason):
                parts.append(reason)
                break
    return "/".join(parts) or None


def parse_interaction(data: bytes) -> LLMReply:
    """Interaction resource (HTTP 200 body) -> LLMReply. Raises ProviderFailure for anything unexpected."""
    try:
        payload = json.loads(data)
        if not isinstance(payload, dict):
            raise ValueError("unexpected envelope")
        status = payload.get("status")
        if status in {"failed", "cancelled"}:
            raise ProviderFailure("LLM_PROVIDER_REJECTED")
        if status not in {"completed", "incomplete", "requires_continuation"}:
            # in_progress / requires_action (tool use) / unknown or missing status
            raise ValueError("unexpected status")
        steps = payload.get("steps", [])
        if not isinstance(steps, list):
            raise ValueError("unexpected steps")
        texts = []
        for step in steps:
            if not isinstance(step, dict):
                raise ValueError("unexpected step")
            kind = step.get("type")
            if kind in {"thought", "user_input"}:
                continue  # Reasoning summaries and echoed input are never used.
            if kind != "model_output":
                raise ValueError("unexpected step type")  # function_call and other *_call steps
            content = step.get("content")
            if not isinstance(content, list):
                raise ValueError("unexpected content")
            for item in content:
                if (
                    not isinstance(item, dict)
                    or item.get("type") != "text"
                    or not isinstance(item.get("text"), str)
                ):
                    raise ValueError("unexpected content item")
                texts.append(item["text"])
        complete = status == "completed"
        if complete and not texts:
            raise ValueError("no model text")
        usage = payload.get("usage")
        usage = usage if isinstance(usage, dict) else {}
        return LLMReply(
            text="".join(texts),
            complete=complete,
            usage={
                USAGE_NAMES[key]: value
                for key, value in usage.items()
                if key in USAGE_NAMES
                and isinstance(value, int)
                and not isinstance(value, bool)
                and value >= 0
            },
        )
    # RecursionError: a deeply nested JSON body is a malformed response like any other.
    except (ValueError, KeyError, TypeError, IndexError, RecursionError) as exc:
        raise ProviderFailure("LLM_MALFORMED_RESPONSE") from exc


class GeminiClient:
    def __init__(self, config=None, transport=None):
        self.config = config or GeminiConfig.from_env()
        self.transport = transport
        # Diagnostics of the last call for scripts/gemini_check.py (never the body or the key).
        self.last_status: int | None = None
        self.last_latency_ms: int | None = None

    def request_body(self, payload: dict) -> dict:
        generation_config = {"max_output_tokens": MAX_OUTPUT_TOKENS}
        if self.config.thinking_level:
            generation_config["thinking_level"] = self.config.thinking_level
        return {
            "model": self.config.model,
            "system_instruction": SELECTOR_SYSTEM_PROMPT,
            "input": json.dumps(payload, ensure_ascii=False),
            "generation_config": generation_config,
            "response_format": {
                "type": "text",
                "mime_type": "application/json",
                "schema": DRAFT_RESPONSE_SCHEMA,
            },
            "store": False,
        }

    async def generate(self, context, request, errors=None):
        return await self.complete(selector_payload(context, request, errors))

    async def complete(self, payload: dict) -> LLMReply:
        body = self.request_body(payload)
        self.last_status = self.last_latency_ms = None
        started = time.monotonic()
        try:
            return await asyncio.wait_for(self._request(body), timeout=REQUEST_TIMEOUT_SECONDS)
        except (httpx.HTTPError, TimeoutError) as exc:
            raise ProviderFailure("LLM_PROVIDER_UNAVAILABLE", True) from exc
        finally:
            self.last_latency_ms = int((time.monotonic() - started) * 1000)

    async def _request(self, body):
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(20, connect=3),
            follow_redirects=False,
            trust_env=False,
            transport=self.transport,
        ) as client:
            async with client.stream(
                "POST",
                self.config.endpoint,
                headers={"x-goog-api-key": self.config.api_key},
                json=body,
            ) as response:
                self.last_status = response.status_code
                if response.status_code != 200:
                    await self._reject(response)
                declared = response.headers.get("content-length", "")
                if declared.isascii() and declared.isdigit() and int(declared) > MAX_RESPONSE_BYTES:
                    raise ProviderFailure("LLM_RESPONSE_TOO_LARGE")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise ProviderFailure("LLM_RESPONSE_TOO_LARGE")
        return parse_interaction(bytes(data))

    @staticmethod
    async def _reject(response):
        retryable = response.status_code in RETRYABLE_STATUS
        head = bytearray()
        try:
            async for chunk in response.aiter_bytes():
                head.extend(chunk)
                if len(head) > MAX_ERROR_BODY_BYTES:
                    break
        except httpx.HTTPError:
            pass  # The status alone decides; the code is only a diagnostic.
        code = provider_error_code(bytes(head)) if len(head) <= MAX_ERROR_BODY_BYTES else None
        # Status and identifier-like code only: never the message, the body or the key.
        logger.warning(
            "Gemini API rejected the request: status=%d code=%s retryable=%s",
            response.status_code,
            code or "-",
            retryable,
        )
        raise ProviderFailure(
            "LLM_PROVIDER_REJECTED",
            retryable,
            http_status=response.status_code,
            provider_code=code,
        )

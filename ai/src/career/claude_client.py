"""Anthropic Claude API client for the curriculum selector (CURRICULUM_LLM_PROVIDER=claude, the default).

Uses the official Anthropic Python SDK (`anthropic` 1.x) and the Messages API:
POST https://api.anthropic.com/v1/messages

- The key (ANTHROPIC_API_KEY) is passed to the SDK explicitly, so the SDK reads no other credential
  source (ANTHROPIC_AUTH_TOKEN, profiles). It travels only in the `x-api-key` header: never in the
  URL, logs, errors, public metadata or audit entries. The endpoint is fixed (ANTHROPIC_BASE_URL is
  not read).
- The request carries the same SELECTOR_SYSTEM_PROMPT and selector_payload as the other providers
  (no VC data, no user identity, no job documents, no URLs) and asks for JSON constrained by a
  schema derived from CurriculumDraft (`output_config.format`, structured outputs).
- Thinking is left at the model default (adaptive on Claude Sonnet 5.5); `output_config.effort`
  (CURRICULUM_LLM_EFFORT, default low) sets how much it thinks. temperature/top_p/top_k are never
  sent (rejected by current models).
- On models that support it, a safety refusal is retried server-side on a fallback model inside the
  same call (`fallbacks: "default"`, beta server-side-fallback-2026-07-01). Text written before a
  fallback switch is dropped; only the model that finished is used.
- The model reply stays untrusted: curriculum_generator parses and validates it and may discard it.
- No SDK retries (the generator decides), no redirects, no proxy environment variables, one bounded
  call: 20 s per read (3 s connect), 25 s overall, at most 256 KiB of model text.
- Concurrency across requests is capped by llm_client.llm_slot (CURRICULUM_LLM_MAX_CONCURRENCY).
- The SDK is imported only when a call is made, so reading the settings (generator info, BE tests
  that run this app in-process) does not need the `anthropic` package.

Failure mapping (ProviderFailure.code, retryable):
  HTTP 408/429/500/502/503/504/529          -> LLM_PROVIDER_REJECTED, retryable
  any other HTTP error (400, 401, 403, 404, 413 ...)
                                            -> LLM_PROVIDER_REJECTED, not retryable
  stop_reason refusal (after any fallback)  -> LLM_PROVIDER_REJECTED, not retryable
  connection errors / timeouts              -> LLM_PROVIDER_UNAVAILABLE, retryable
  stop_reason max_tokens                    -> reply with complete=False (generator retries once, then falls back)
  anything else unexpected                  -> LLM_MALFORMED_RESPONSE
  model text over 256 KiB                   -> LLM_RESPONSE_TOO_LARGE
"""

import asyncio
import json
import logging
import os
import re
import time
from dataclasses import dataclass, field

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

PROVIDER = "claude"
# Fixed host. Tests inject an httpx2 transport instead of overriding the endpoint.
ANTHROPIC_API_BASE = "https://api.anthropic.com"
DEFAULT_MODEL = "claude-sonnet-5-5"
EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
# Picking listed IDs and hours under fixed rules is a short, latency-bound task: low keeps it fast
# and cheap. The output is validated either way (a bad draft is retried once, then discarded).
DEFAULT_EFFORT = "low"
MAX_OUTPUT_TOKENS = 16000  # Includes thinking tokens. Billing counts only what is generated.
REQUEST_TIMEOUT_SECONDS = 25
MAX_RESPONSE_BYTES = 262144
RETRYABLE_STATUS = frozenset({408, 429, 500, 502, 503, 504, 529})
# Provider error types are logged only when they look like an identifier (never the message).
SAFE_PROVIDER_CODE = re.compile(r"[A-Za-z][A-Za-z0-9_]{0,63}")
FALLBACK_BETA = "server-side-fallback-2026-07-01"
# Models that accept server-side refusal fallbacks (fallbacks: "default").
FALLBACK_MODELS = frozenset(
    {"claude-sonnet-5-5", "claude-opus-5-5", "claude-opus-5", "claude-fable-5-1", "claude-fable-5-2"}
)

# Response schema derived from CurriculumDraft using only keywords structured outputs accept
# (type, properties, required, additionalProperties: false, items, anyOf, format: uuid). Hour limits
# and the step count are not expressible there; the raw pydantic schema (with them) still goes to
# the model inside the prompt payload (PROMPT_VERSION unchanged). output_validator and pydantic stay
# authoritative for UUIDs, coverage, order, resource IDs, hours and budgets.
# tests/career/test_claude_client.py keeps this in step with CurriculumDraft.
DRAFT_RESPONSE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["steps"],
    "properties": {
        "steps": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["step_id", "resource_version_id", "hours"],
                "properties": {
                    "step_id": {"type": "string", "format": "uuid"},
                    "resource_version_id": {
                        "anyOf": [{"type": "string", "format": "uuid"}, {"type": "null"}]
                    },
                    "hours": {"type": "integer"},
                },
            },
        }
    },
}

USAGE_NAMES = {"input_tokens": "prompt_tokens", "output_tokens": "completion_tokens"}


def _valid_key(key: str) -> bool:
    # The key goes into an HTTP header: printable ASCII without whitespace. No prefix is enforced.
    return bool(key) and key.isascii() and key.isprintable() and not any(ch.isspace() for ch in key)


def _safe_code(value) -> str | None:
    return value if isinstance(value, str) and SAFE_PROVIDER_CODE.fullmatch(value) else None


@dataclass
class ClaudeConfig:
    model: str
    effort: str
    api_key: str = field(repr=False)

    provider = PROVIDER

    @classmethod
    def from_env(cls):
        """Read the Claude settings. Messages name variables and rules only, never values."""
        if os.getenv("CURRICULUM_LLM_ENABLED", "false").lower() != "true":
            raise ConfigurationError("CURRICULUM_LLM_ENABLED=true is required for the curriculum LLM")
        key = os.getenv("ANTHROPIC_API_KEY", "")
        if not _valid_key(key):
            raise ConfigurationError(
                "ANTHROPIC_API_KEY is required (printable ASCII without spaces or line breaks)"
            )
        model = os.getenv("CURRICULUM_LLM_MODEL", "").strip() or DEFAULT_MODEL
        if not MODEL_ID_PATTERN.fullmatch(model) or not model.startswith("claude-"):
            raise ConfigurationError(
                "CURRICULUM_LLM_MODEL must be a Claude model ID such as " + DEFAULT_MODEL
            )
        effort = os.getenv("CURRICULUM_LLM_EFFORT", "").strip().lower() or DEFAULT_EFFORT
        if effort not in EFFORT_LEVELS:
            raise ConfigurationError(
                "CURRICULUM_LLM_EFFORT must be one of: low, medium, high, xhigh, max "
                "(empty means low)"
            )
        return cls(model, effort, key)

    @property
    def refusal_fallback(self) -> bool:
        return self.model in FALLBACK_MODELS

    def public_metadata(self):
        # Frozen into the job by BE (GenerationInput.generator) and compared before generating.
        return {
            "provider": PROVIDER,
            "api": "messages",
            "model": self.model,
            "effort": self.effort,
            "refusal_fallback": self.refusal_fallback,
            "max_output_tokens": MAX_OUTPUT_TOKENS,
            "prompt_version": PROMPT_VERSION,
        }

    def create_client(self):
        return ClaudeClient(self)


def parse_message(message) -> LLMReply:
    """Messages API response -> LLMReply. Raises ProviderFailure for anything unexpected."""
    try:
        stop = message.stop_reason
        if stop == "refusal":
            details = getattr(message, "stop_details", None)
            category = _safe_code(getattr(details, "category", None))
            raise ProviderFailure(
                "LLM_PROVIDER_REJECTED",
                provider_code="refusal" + (f"/{category}" if category else ""),
            )
        if stop not in {"end_turn", "max_tokens"}:
            # tool_use, pause_turn, stop_sequence or unknown: never sent tools or stop sequences.
            raise ValueError("unexpected stop reason")
        texts = []
        for block in message.content:
            kind = block.type
            if kind in {"thinking", "redacted_thinking"}:
                continue  # Reasoning is never used (and is empty by default).
            if kind == "fallback":
                texts = []  # A model declined; only what the model that finished wrote counts.
                continue
            if kind != "text" or not isinstance(block.text, str):
                raise ValueError("unexpected content block")
            texts.append(block.text)
        complete = stop == "end_turn"
        text = "".join(texts)
        if complete and not text:
            raise ValueError("no model text")
        if len(text.encode("utf-8", errors="backslashreplace")) > MAX_RESPONSE_BYTES:
            raise ProviderFailure("LLM_RESPONSE_TOO_LARGE")
        usage = {}
        for name, alias in USAGE_NAMES.items():
            value = getattr(message.usage, name, None)
            if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                usage[alias] = value
        if len(usage) == 2:
            usage["total_tokens"] = usage["prompt_tokens"] + usage["completion_tokens"]
        return LLMReply(text=text, complete=complete, usage=usage)
    except (ValueError, KeyError, TypeError, IndexError, AttributeError) as exc:
        raise ProviderFailure("LLM_MALFORMED_RESPONSE") from exc


class ClaudeClient:
    def __init__(self, config=None, transport=None):
        self.config = config or ClaudeConfig.from_env()
        self.transport = transport  # httpx2 transport (tests); None means the network.
        # Diagnostics of the last call for scripts/claude_check.py (never the body or the key).
        self.last_status: int | None = None
        self.last_latency_ms: int | None = None
        self.last_model: str | None = None

    def request_body(self, payload: dict) -> dict:
        body = {
            "model": self.config.model,
            "max_tokens": MAX_OUTPUT_TOKENS,
            "system": SELECTOR_SYSTEM_PROMPT,
            "messages": [{"role": "user", "content": json.dumps(payload, ensure_ascii=False)}],
            "output_config": {
                "effort": self.config.effort,
                "format": {"type": "json_schema", "schema": DRAFT_RESPONSE_SCHEMA},
            },
        }
        if self.config.refusal_fallback:
            body["betas"] = [FALLBACK_BETA]
            body["fallbacks"] = "default"
        return body

    async def generate(self, context, request, errors=None):
        return await self.complete(selector_payload(context, request, errors))

    async def complete(self, payload: dict) -> LLMReply:
        import anthropic

        body = self.request_body(payload)
        self.last_status = self.last_latency_ms = self.last_model = None
        started = time.monotonic()
        try:
            message = await asyncio.wait_for(self._request(body), timeout=REQUEST_TIMEOUT_SECONDS)
        except anthropic.APIStatusError as exc:
            self.last_status = exc.status_code
            # from None: the SDK error carries the provider's message, which never leaves this call.
            raise self._rejection(exc) from None
        except (anthropic.APIConnectionError, TimeoutError) as exc:
            raise ProviderFailure("LLM_PROVIDER_UNAVAILABLE", True) from exc
        except (anthropic.APIResponseValidationError, ValueError, TypeError, RecursionError) as exc:
            # A 200 body that is not JSON (ValueError) or not a message: malformed like any other.
            raise ProviderFailure("LLM_MALFORMED_RESPONSE") from exc
        except anthropic.AnthropicError as exc:
            raise ProviderFailure("LLM_PROVIDER_REJECTED") from exc
        finally:
            self.last_latency_ms = int((time.monotonic() - started) * 1000)
        self.last_status = 200
        model = getattr(message, "model", None)
        self.last_model = model if isinstance(model, str) else None
        return parse_message(message)

    async def _request(self, body):
        import anthropic
        from anthropic import AsyncAnthropic, DefaultAsyncHttpxClient

        http_client = DefaultAsyncHttpxClient(
            trust_env=False, follow_redirects=False, transport=self.transport
        )
        async with AsyncAnthropic(
            api_key=self.config.api_key,
            base_url=ANTHROPIC_API_BASE,
            max_retries=0,
            timeout=anthropic.Timeout(20.0, connect=3.0),
            http_client=http_client,
        ) as client:
            return await client.beta.messages.create(**body)

    @staticmethod
    def _rejection(exc) -> ProviderFailure:
        status = exc.status_code
        retryable = status in RETRYABLE_STATUS
        body = exc.body if isinstance(exc.body, dict) else {}
        error = body.get("error") if isinstance(body.get("error"), dict) else body
        code = _safe_code(error.get("type")) if isinstance(error, dict) else None
        # Status and identifier-like error type only: never the message, the body or the key.
        logger.warning(
            "Claude API rejected the request: status=%d type=%s retryable=%s",
            status,
            code or "-",
            retryable,
        )
        return ProviderFailure(
            "LLM_PROVIDER_REJECTED", retryable, http_status=status, provider_code=code
        )

"""Curriculum selector on the pinned research model (reuses src.llm.transformers_llm).

The same constrained selector prompt as the HTTPS client is used, so the output is
still an untrusted JSON draft that curriculum_generator validates and may discard.
The research bounds of TransformersLLM (input <= 2048, output <= 256 tokens, greedy,
fixed seed and revision) are kept; a payload that does not fit fails explicitly
instead of being truncated.
"""

import asyncio
import json
import os
import threading

from src.career.errors import ConfigurationError
from src.career.contracts.curriculum import PROMPT_VERSION
from src.career.llm_client import (
    SELECTOR_SYSTEM_PROMPT,
    LLMReply,
    ProviderFailure,
    selector_payload,
)
from src.llm.transformers_llm import MODEL_ID, REVISION

PROVIDER = "local-transformers"
MAX_NEW_TOKENS = 256
MAX_INPUT_TOKENS = 2048

_model = None
_model_lock = threading.Lock()  # One GPU model per process; generations are serialized.


def _load_model():
    global _model
    with _model_lock:
        if _model is None:
            from src.llm.transformers_llm import TransformersLLM

            _model = TransformersLLM(
                max_new_tokens=MAX_NEW_TOKENS,
                max_input_tokens=MAX_INPUT_TOKENS,
                local_files_only=os.getenv("CURRICULUM_LOCAL_ALLOW_DOWNLOAD", "false") != "true",
            )
        return _model


class LocalSelectorConfig:
    provider = PROVIDER
    model = MODEL_ID

    @classmethod
    def from_env(cls):
        if os.getenv("CURRICULUM_LLM_ENABLED", "false").lower() != "true":
            raise ConfigurationError("Explicit local generator configuration is required")
        return cls()

    def public_metadata(self):
        return {
            "provider": PROVIDER,
            "model": MODEL_ID,
            "revision": REVISION,
            "max_output_tokens": MAX_NEW_TOKENS,
            "max_input_tokens": MAX_INPUT_TOKENS,
            "prompt_version": PROMPT_VERSION,
        }

    def create_client(self):
        return LocalSelectorClient()


class LocalSelectorClient:
    def __init__(self, model_loader=None):
        self.model_loader = model_loader or _load_model

    def _run(self, messages):
        model = self.model_loader()
        with _model_lock:
            text = model.generate(messages)
            return text, dict(model.last_generation or {})

    async def generate(self, context, request, errors=None):
        messages = [
            {"role": "system", "content": SELECTOR_SYSTEM_PROMPT},
            {
                "role": "user",
                "content": json.dumps(
                    selector_payload(context, request, errors), ensure_ascii=False
                ),
            },
        ]
        try:
            text, meta = await asyncio.to_thread(self._run, messages)
        except ValueError as exc:
            # TransformersLLM refuses (never truncates) inputs over the token limit.
            raise ProviderFailure("LLM_CONTEXT_TOO_LARGE") from exc
        except (RuntimeError, OSError, ImportError) as exc:
            raise ProviderFailure("LOCAL_MODEL_UNAVAILABLE") from exc
        usage = meta.get("token_usage") or {}
        names = {
            "input_tokens": "prompt_tokens",
            "output_tokens": "completion_tokens",
            "total_tokens": "total_tokens",
        }
        return LLMReply(
            text=text,
            complete=meta.get("finish_reason") == "eos",
            usage={
                names[key]: value
                for key, value in usage.items()
                if key in names and isinstance(value, int) and not isinstance(value, bool)
            },
        )

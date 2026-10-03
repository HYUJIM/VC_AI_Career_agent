from typing import Protocol
import httpx

class LocalLLM(Protocol):
    def generate(self, prompt: str) -> str: ...

class MockLLM:
    """Fixed output only; must never be reported as an LLM experiment."""
    def generate(self, prompt):
        return "[MOCK] Pipeline connectivity test only; no model inference performed."

class OpenAICompatibleLLM:
    """Optional local server adapter; requires server support for all parameters."""
    def __init__(self, base_url, config):
        self.base_url, self.config = base_url.rstrip("/"), config
    def generate(self, prompt):
        c = self.config
        response = httpx.post(self.base_url + "/chat/completions", json={
            "model": c.name, "temperature": c.temperature, "seed": c.seed,
            "max_tokens": c.max_tokens, "messages": [{"role": "user", "content": prompt}]
        }, timeout=180)
        response.raise_for_status()
        answer = response.json()["choices"][0]["message"]["content"]
        if not isinstance(answer, str):
            raise ValueError("Model response content must be a string")
        return answer

from dataclasses import dataclass, asdict
import math
import os
from dotenv import load_dotenv

@dataclass(frozen=True)
class ModelConfig:
    name: str
    version_or_quant: str
    temperature: float
    seed: int | None
    max_tokens: int
    def __post_init__(self):
        if not self.name or not self.version_or_quant or not math.isfinite(self.temperature) or self.temperature < 0 or self.max_tokens < 1:
            raise ValueError("Invalid model configuration")
    def metadata(self):
        return asdict(self)

def from_env():
    load_dotenv()
    def required(key):
        value = os.getenv(key, "").strip()
        if not value:
            raise ValueError(f"Set {key} explicitly in .env")
        return value
    config = ModelConfig(required("LLM_MODEL"), required("LLM_VERSION_OR_QUANT"), float(required("LLM_TEMPERATURE")), int(required("LLM_SEED")), int(required("LLM_MAX_TOKENS")))
    return config, required("LLM_PROVIDER"), required("LLM_BASE_URL")

"""BE <-> AI shared contract.

The canonical source is ai/src/career/contracts. BE keeps a byte-identical copy in
BE/app/ai_contract, refreshed with `python scripts/sync_contract_to_be.py` from `ai/`. Both sides send and
check CONTRACT_ID, so a copy that drifted fails closed instead of silently mis-parsing.
Only relative imports are allowed in this package so that the copy needs no rewriting.
"""

import hashlib
from pathlib import Path

CONTRACT_VERSION = "career-ai/2"
CONTRACT_HEADER = "X-Career-AI-Contract"
CONTRACT_FILES = ("api.py", "career.py", "curriculum.py", "verification.py")


def _fingerprint() -> str:
    digest = hashlib.sha256()
    for name in CONTRACT_FILES:
        # Normalize checkout line endings (e.g. Windows autocrlf) before hashing.
        text = Path(__file__).with_name(name).read_text(encoding="utf-8").replace("\r\n", "\n")
        digest.update(name.encode() + b"\0" + text.encode() + b"\0")
    return digest.hexdigest()


CONTRACT_ID = f"{CONTRACT_VERSION}+{_fingerprint()[:16]}"

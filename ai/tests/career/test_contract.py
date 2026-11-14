"""The contract package must stay copyable to BE byte-for-byte."""

import re
from pathlib import Path

import pytest

from src.career import contracts
from src.career.contracts import CONTRACT_FILES, CONTRACT_ID, CONTRACT_VERSION

PACKAGE = Path(contracts.__file__).parent
BE_COPY = Path(__file__).resolve().parents[3] / "BE" / "app" / "ai_contract"


def test_contract_id_is_version_plus_fingerprint():
    assert re.fullmatch(re.escape(CONTRACT_VERSION) + r"\+[0-9a-f]{16}", CONTRACT_ID)


@pytest.mark.parametrize("name", [*CONTRACT_FILES, "__init__.py"])
def test_only_relative_or_third_party_imports(name):
    text = (PACKAGE / name).read_text(encoding="utf-8")
    assert not re.search(r"^\s*(from|import)\s+(src|app)\b", text, re.MULTILINE)


def test_sibling_be_copy_is_identical_when_present():
    if not BE_COPY.exists():
        pytest.skip("BE folder is not present next to ai/")
    for name in [*CONTRACT_FILES, "__init__.py"]:
        assert (BE_COPY / name).read_bytes() == (PACKAGE / name).read_bytes(), (
            f"{name} drifted; run: python scripts/sync_contract_to_be.py"
        )

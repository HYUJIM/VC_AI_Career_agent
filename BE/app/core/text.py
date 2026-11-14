"""요청 텍스트 검사: DB에 저장할 수 없는 문자와 한 줄 텍스트.

- NUL(U+0000)과 짝 없는 서로게이트(U+D800–U+DFFF)는 PostgreSQL text·JSONB에 저장할 수 없다
  (psycopg가 NUL을 거부하고, 서로게이트는 UTF-8로 인코딩되지 않는다). 받으면 500이 아니라 422로 돌려준다.
  json.loads는 이스케이프된 서로게이트 쌍(이모지)을 한 글자로 합치므로, 이 검사에 걸리는 것은 짝 없는 서로게이트뿐이다.
- 한 줄 텍스트(가입 이름·이메일, 포트폴리오 제목·희망 직무): 줄바꿈·탭 등 제어 문자(Unicode Cc)와
  줄·문단 구분자(U+2028·U+2029)를 받지 않는다. 포트폴리오 Markdown 머리말에 줄을 끼워 넣지 못하게 한다.
  이모지 결합 문자(ZWJ, Cf)·새 이모지(Cn)·NBSP(Zs)는 받는다.
"""
import re
import unicodedata
from typing import Annotated, Any, Optional

from pydantic import AfterValidator

UNSTORABLE_TEXT_MESSAGE = "must not contain NUL or unpaired surrogate characters"
SINGLE_LINE_MESSAGE = "must be a single line without control characters"

_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")
_NOT_SINGLE_LINE = frozenset({"Cc", "Zl", "Zp"})


def has_unstorable_text(value: Any) -> bool:
    """value(문자열, dict의 키·값, list·tuple 항목, 중첩 포함)에 저장할 수 없는 문자가 있는지.

    깊게 중첩된 JSON에서도 RecursionError가 나지 않도록 재귀 없이 명시적 스택으로 돈다.
    """
    stack = [value]
    while stack:
        item = stack.pop()
        if isinstance(item, str):
            if _UNSTORABLE.search(item):
                return True
        elif isinstance(item, dict):
            stack.extend(item.keys())
            stack.extend(item.values())
        elif isinstance(item, (list, tuple)):
            stack.extend(item)
    return False


def ensure_storable(value: Optional[str]) -> Optional[str]:
    """pydantic 검사기: 저장할 수 없는 문자가 있으면 ValueError(→ 422)."""
    if value is not None and _UNSTORABLE.search(value):
        raise ValueError(UNSTORABLE_TEXT_MESSAGE)
    return value


def ensure_single_line(value: Optional[str]) -> Optional[str]:
    """pydantic 검사기: 제어 문자(Cc)·U+2028·U+2029가 있으면 ValueError(→ 422). 앞뒤 공백 정리는 호출하는 쪽이 한다."""
    if value is not None and any(unicodedata.category(ch) in _NOT_SINGLE_LINE for ch in value):
        raise ValueError(SINGLE_LINE_MESSAGE)
    return value


# 요청 모델의 문자열 필드·경로 변수에 쓴다: `name: StorableText = Field(...)`, `did: StorableText`.
StorableText = Annotated[str, AfterValidator(ensure_storable)]

"""요청 텍스트 검사: DB에 저장할 수 없는 문자와 한 줄 텍스트.

- NUL(U+0000)과 짝 없는 서로게이트(U+D800–U+DFFF)는 PostgreSQL text·JSONB에 저장할 수 없다
  (psycopg가 NUL을 거부하고, 서로게이트는 UTF-8로 인코딩되지 않는다). 받으면 500이 아니라 422로 돌려준다.
  json.loads는 이스케이프된 서로게이트 쌍(이모지)을 한 글자로 합치므로, 이 검사에 걸리는 것은 짝 없는 서로게이트뿐이다.
- 한 줄 텍스트(가입 이름·이메일, 포트폴리오 제목·희망 직무, 공유 링크 이름): 줄바꿈·탭 등 제어 문자(Unicode Cc)와
  줄·문단 구분자(U+2028·U+2029)를 받지 않는다. 포트폴리오 Markdown 머리말에 줄을 끼워 넣지 못하게 한다.
  글자 방향을 바꾸거나 보이지 않는 서식 문자(BIDI_AND_INVISIBLE: U+202E 등)도 받지 않는다. 'abc‮gnp.exe'가 화면에
  'abcexe.png'처럼 보이거나, 폭 없는 공백을 끼운 이름이 다른 사람 이름과 똑같아 보이게 꾸밀 수 있다.
  이모지 결합 문자(ZWJ U+200D, Cf)·새 이모지(Cn)·NBSP(Zs)는 받는다.
"""
import re
import unicodedata
from typing import Annotated, Any, Optional

from pydantic import AfterValidator

UNSTORABLE_TEXT_MESSAGE = "must not contain NUL or unpaired surrogate characters"
SINGLE_LINE_MESSAGE = "must be a single line without control characters"

_UNSTORABLE = re.compile("[\x00\ud800-\udfff]")
_NOT_SINGLE_LINE = frozenset({"Cc", "Zl", "Zp"})
# 글자 방향 제어(U+061C, U+200E·U+200F, U+202A–U+202E, U+2066–U+2069)와 보이지 않는 서식 문자(U+200B·U+200C,
# U+2060–U+2064, U+FEFF). 한 줄 텍스트 검사(ensure_single_line)가 거절하고, 거절할 수 없는 값(OAuth 프로필 이름,
# 발급기관이 서명한 배지 문구)은 strip_invisible로 지운다. ZWJ(U+200D)는 이모지 묶음(👩‍💻)에 쓰이므로 넣지 않는다.
BIDI_AND_INVISIBLE = frozenset(
    "؜​‌‎‏‪‫‬‭‮"
    "⁠⁡⁢⁣⁤⁦⁧⁨⁩﻿"
)


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


# 위 목록 밖의 보이지 않는 글자도 같게 다룬다: 그 밖의 서식 문자(Unicode Cf: 소프트 하이픈 U+00AD, 몽골 모음 구분자 U+180E,
# 태그 문자 U+E0001·U+E0020–U+E007F 등)와 빈칸처럼 보이는 글자(BLANK_LOOKING: 한글 채움 문자 U+115F·U+1160·U+3164·U+FFA0,
# 점자 빈칸 U+2800. 빈 이름처럼 보인다). 태그 문자는 화면에 보이지 않지만 LLM은 읽으므로 숨은 지시를 끼울 수 있다.
# 서식 문자 중 ZWJ(U+200D)만 받는다. 그래서 태그 문자로 만드는 지역 깃발 이모지(잉글랜드·스코틀랜드·웨일스)는 한 줄 텍스트에
# 쓸 수 없다. 이집트 상형문자 서식 문자 U+13430–U+1343F는 Python 3.11의 Unicode 14에 일부가 아직 없어(Cn) 범위로 적는다
# (FE는 더 새로운 Unicode로 Cf라 거절한다. 두 쪽 규칙을 같게 둔다).
BLANK_LOOKING = frozenset("\u115f\u1160\u3164\uffa0\u2800")
ZWJ = "\u200d"
_EGYPTIAN_FORMAT = range(0x13430, 0x13440)
# Unicode Default_Ignorable_Code_Point 가운데 Cf가 아닌 것 (브라우저가 폭 0으로 그린다): 쓰지 않게 된 크메르 모음 U+17B4·U+17B5와
# 아직 배정되지 않은(Cn) U+2065, U+FFF0–U+FFF8, U+E0000–U+E00FF, U+E01F0–U+E0FFF. 이형 선택자(U+FE00–U+FE0F, 일본어 이름의
# U+E0100–U+E01EF)·CGJ U+034F·몽골 자유 이형 선택자 U+180B–U+180D·U+180F는 실제 글자 모양에 쓰이므로 여기에 넣지 않는다
# (그것만으로 된 값은 has_visible_text가 거절한다).
_IGNORABLE_RANGES = ((0x17B4, 0x17B5), (0x2065, 0x2065), (0xFFF0, 0xFFF8), (0xE0000, 0xE00FF), (0xE01F0, 0xE0FFF))


def is_invisible(ch: str) -> bool:
    """보이지 않거나 글자 방향을 바꾸는 글자인지: BIDI_AND_INVISIBLE, ZWJ를 뺀 Unicode Cf, BLANK_LOOKING, U+13430–U+1343F,
    _IGNORABLE_RANGES."""
    code = ord(ch)
    return (ch in BIDI_AND_INVISIBLE or ch in BLANK_LOOKING or code in _EGYPTIAN_FORMAT
            or any(low <= code <= high for low, high in _IGNORABLE_RANGES)
            or (ch != ZWJ and unicodedata.category(ch) == "Cf"))


# 이름에는 받지만(글자 모양에 쓰임) 이메일에는 받지 않는 폭 0 글자: CGJ, 몽골 자유 이형 선택자, 이형 선택자(U+FE00–U+FE0F),
# 일본어 IVS(U+E0100–U+E01EF), U+FFFC. 이메일에 끼우면 다른 계정 이메일과 똑같아 보인다(예: victim+U+FE00@example.com).
_EMAIL_EXTRA_IGNORABLE = ((0x034F, 0x034F), (0x180B, 0x180D), (0x180F, 0x180F), (0xFE00, 0xFE0F), (0xE0100, 0xE01EF),
                          (0xFFFC, 0xFFFC))


def is_email_confusable(ch: str) -> bool:
    """이메일에 두지 않는 보이지 않는 글자: Cf 전부(ZWJ 포함), is_invisible, _EMAIL_EXTRA_IGNORABLE."""
    code = ord(ch)
    return (unicodedata.category(ch) == "Cf" or is_invisible(ch)
            or any(low <= code <= high for low, high in _EMAIL_EXTRA_IGNORABLE))


def has_visible_text(value: str) -> bool:
    """눈에 보이는 글자(문자·숫자·문장부호·기호, 이 Python이 모르는 새 글자 Cn)가 하나라도 있는지.

    결합 문자·이형 선택자(U+FE0F 등)·CGJ·ZWJ·공백만으로 된 값은 화면에 빈칸처럼 보이므로 이름·제목으로 받지 않는다.
    """
    for ch in value:
        category = unicodedata.category(ch)
        if (category[0] in "LNPS" or category == "Cn") and not is_invisible(ch):
            return True
    return False


def ensure_single_line(value: Optional[str]) -> Optional[str]:
    """pydantic 검사기: 제어 문자(Cc)·U+2028·U+2029·보이지 않는 글자(is_invisible)가 있거나, 비어 있지 않은데 보이는 글자가
    하나도 없으면(has_visible_text) ValueError(→ 422). 앞뒤 공백 정리는 호출하는 쪽이 한다."""
    if value is not None and (
        any(unicodedata.category(ch) in _NOT_SINGLE_LINE or is_invisible(ch) for ch in value)
        or (value and not has_visible_text(value))
    ):
        raise ValueError(SINGLE_LINE_MESSAGE)
    return value


def strip_invisible(value: str) -> str:
    """보이지 않는 글자(is_invisible)를 지운다. 거절하지 않고 고쳐 쓰는 값(OAuth 프로필 이름, 발급기관이 서명한 배지 문구)에
    쓴다. 그 밖의 글자는 그대로다."""
    return "".join(ch for ch in value if not is_invisible(ch))


# 요청 모델의 문자열 필드·경로 변수에 쓴다: `name: StorableText = Field(...)`, `did: StorableText`.
StorableText = Annotated[str, AfterValidator(ensure_storable)]

import re
import sys
import unicodedata

from src.career.contracts.career import SkillCatalog, SkillDefinition
from src.career.errors import EvidenceError

# NFKC는 글자 하나를 최대 18자로 늘리고(U+FDFA), 기술·논리 검사는 모두 정규화된 텍스트 위에서 돈다.
# 연구자가 보낸 원문·인용은 한 번만 정규화하고(bounded_normalized_text), 그 결과가 원래 길이의
# 2배 + 64자를 넘으면 거절한다. 실제 공고는 몇 글자만 늘어난다(㈜ → (주)). 64자 여유는 그런
# 문자만으로 된 아주 짧은 인용도 받기 위한 것이다.
MAX_NORMALIZED_GROWTH = 2
NORMALIZED_GROWTH_SLACK = 64

CATALOG = SkillCatalog(
    skills=[
        SkillDefinition(skill_id="sql", label="SQL", aliases=["SQL"]),
        SkillDefinition(skill_id="python", label="Python", aliases=["Python", "Python3"]),
        SkillDefinition(skill_id="scala", label="Scala", aliases=["Scala"]),
        SkillDefinition(
            skill_id="spark", label="Apache Spark", aliases=["Apache Spark", "Spark", "PySpark"]
        ),
        SkillDefinition(skill_id="kafka", label="Apache Kafka", aliases=["Apache Kafka", "Kafka"]),
        SkillDefinition(
            skill_id="airflow", label="Apache Airflow", aliases=["Apache Airflow", "Airflow"]
        ),
        SkillDefinition(skill_id="etl", label="ETL", aliases=["ETL"]),
        SkillDefinition(
            skill_id="data_modeling",
            label="데이터 모델링",
            aliases=["데이터 모델링", "data modeling"],
        ),
        SkillDefinition(skill_id="git", label="Git", aliases=["Git"]),
        SkillDefinition(skill_id="linux", label="Linux", aliases=["Linux", "리눅스"]),
        SkillDefinition(skill_id="aws", label="AWS", aliases=["AWS", "Amazon Web Services"]),
        SkillDefinition(skill_id="docker", label="Docker", aliases=["Docker", "도커"]),
    ]
)


# NFKC는 결합 문자(악센트처럼 앞 글자에 붙는 문자)가 이어진 구간을 삽입 정렬로 다시 늘어놓는다. 그런
# 문자만 길게 이어 붙인 텍스트는 길이의 제곱만큼 시간이 들고(10만 자에 약 2초) 그동안 GIL을 놓지 않아
# /health까지 멈춘다. 글자 수는 늘지 않으므로 위의 2배 + 64자 상한에도 걸리지 않는다. 그래서 정규화 전에
# 연속 구간을 MAX_COMBINING_RUN자에서 자른다(UAX #15 Stream-Safe Text의 상한과 같다). 실제 문장에는
# 결합 문자가 30개를 넘게 이어지지 않으므로 결과는 그대로다.
MAX_COMBINING_RUN = 30


def _char_class(points) -> str:
    """정렬된 코드 포인트를 정규식 문자 클래스의 본문으로 바꾼다 (이어진 값은 범위 하나로 묶는다)."""
    ranges = []
    for point in points:
        if ranges and point == ranges[-1][1] + 1:
            ranges[-1][1] = point
        else:
            ranges.append([point, point])
    return "".join(chr(first) + "-" + chr(last) for first, last in ranges)


# NFKD로 풀면 결합 문자만 남는 문자: 결합 문자 자신과, 풀려서 결합 문자가 되는 몇 글자(U+0F73, U+FF9E 등).
# 뒤쪽을 빼면 그 글자만 이어 붙여 같은 제곱 시간을 만들 수 있다.
_COMBINING = [
    point
    for point in range(sys.maxunicode + 1)
    if unicodedata.combining(chr(point))
    or (
        unicodedata.decomposition(chr(point))
        and all(map(unicodedata.combining, unicodedata.normalize("NFKD", chr(point))))
    )
]
_COMBINING_CLASS = "[" + _char_class(_COMBINING) + "]"
# 첫 글자만 값싼 클래스(BMP의 결합 문자 + BMP 밖 전부)로 훑고 뒤돌아보기로 확인한다. 전체 클래스는 BMP 밖
# 범위가 많아서 글자마다 대조하면 결합 문자가 없는 보통 텍스트의 정규화가 10배쯤 느려진다.
_COMBINING_CANDIDATE = (
    "[" + _char_class(point for point in _COMBINING if point <= 0xFFFF) + "\U00010000-\U0010ffff]"
)
_LONG_COMBINING_RUN = re.compile(
    "(%s(?<=%s)%s{%d})%s+"
    % (
        _COMBINING_CANDIDATE,
        _COMBINING_CLASS,
        _COMBINING_CLASS,
        MAX_COMBINING_RUN - 1,
        _COMBINING_CLASS,
    )
)


def normalized_text(value: str) -> str:
    # 31번째부터의 결합 문자를 버린다. 그런 구간이 없는 보통 텍스트는 치환(sub)까지 가지 않는다: ASCII에는 결합
    # 문자가 없고, 그 밖의 텍스트는 search 한 번으로 확인한다. sub는 맞는 곳이 없어도 호출마다 치환 템플릿(\1)을
    # 처리해서, 별칭·기술스택 항목 같은 짧은 문자열의 정규화가 6~20배 느렸다.
    match = None if value.isascii() else _LONG_COMBINING_RUN.search(value)
    if match:
        # 첫 구간까지는 search가 이미 훑었으므로 그 뒤만 치환한다 (구간마다 앞의 30자, 곧 \1만 남는다).
        value = value[: match.end(1)] + _LONG_COMBINING_RUN.sub(r"\1", value[match.end() :])
    return " ".join(unicodedata.normalize("NFKC", value).casefold().split())


def bounded_normalized_text(value: str) -> str:
    """normalized_text와 같되, 정규화로 지나치게 늘어나는 텍스트는 EvidenceError로 거절한다."""
    text = normalized_text(value)
    if len(text) > MAX_NORMALIZED_GROWTH * len(value) + NORMALIZED_GROWTH_SLACK:
        raise EvidenceError("TEXT_EXPANDS_TOO_MUCH_WHEN_NORMALIZED")
    return text


_ALIAS_PATTERNS = {
    skill.skill_id: [
        re.compile(r"(?<![a-z0-9_])" + re.escape(normalized_text(alias)) + r"(?![a-z0-9_])")
        for alias in skill.aliases
    ]
    for skill in CATALOG.skills
}


def mentions_skill(normalized: str, skill_id: str) -> bool:
    """이미 normalized_text를 거친 텍스트용 contains_skill."""
    return any(pattern.search(normalized) for pattern in _ALIAS_PATTERNS[skill_id])


def mentioned_skills(normalized: str) -> list[str]:
    """이미 정규화된 텍스트가 언급하는 카탈로그 기술 ID (카탈로그 순서)."""
    return [skill_id for skill_id in _ALIAS_PATTERNS if mentions_skill(normalized, skill_id)]


def contains_skill(text: str, skill_id: str) -> bool:
    return mentions_skill(normalized_text(text), skill_id)


def exact_skill(value: str):
    return next(
        (
            skill.skill_id
            for skill in CATALOG.skills
            if normalized_text(value) in {normalized_text(alias) for alias in skill.aliases}
        ),
        None,
    )

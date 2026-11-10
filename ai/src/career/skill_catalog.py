import re
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


def normalized_text(value: str) -> str:
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

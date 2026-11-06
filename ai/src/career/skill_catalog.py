import re
import unicodedata

from src.career.contracts.career import SkillCatalog, SkillDefinition

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


def contains_skill(text: str, skill_id: str) -> bool:
    text = normalized_text(text)
    skill = next(item for item in CATALOG.skills if item.skill_id == skill_id)
    return any(
        re.search(r"(?<![a-z0-9_])" + re.escape(normalized_text(alias)) + r"(?![a-z0-9_])", text)
        for alias in skill.aliases
    )


def exact_skill(value: str):
    return next(
        (
            skill.skill_id
            for skill in CATALOG.skills
            if normalized_text(value) in {normalized_text(alias) for alias in skill.aliases}
        ),
        None,
    )

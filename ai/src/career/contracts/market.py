"""직무별 채용 시장 통계 형식 (BE <-> AI).

BE가 수집·저장한 공고를 직무 하나씩 묶어 보내면 AI가 기술 언급을 뽑아 통계(스냅샷)로 돌려준다.
AI는 공고를 저장하지 않는다. 스냅샷에는 공고 원문이 들어가지 않는다.
"""

from typing import Literal

from pydantic import AwareDatetime, Field, HttpUrl, field_validator, model_validator

from .verification import EvidenceModel

MarketRoleID = Literal[
    "bigdata_engineer",
    "privacy_specialist",
    "backend_developer",
    "frontend_developer",
    "infra_engineer",
]
SkillCategory = Literal[
    "language",
    "framework",
    "database",
    "data",
    "cloud",
    "devops",
    "security",
    "privacy",
    "certification",
    "practice",
]
# 공고 안에서 기술이 나온 가장 강한 자리. required(자격요건) > stack(기술스택) > preferred(우대사항) > mentioned(주요업무·제목)
MentionLevel = Literal["required", "stack", "preferred", "mentioned"]
ExperienceBucket = Literal["entry", "junior", "mid", "senior", "unspecified"]

MAX_POSTINGS = 500
MAX_SECTION_CHARS = 20000


class MarketSkill(EvidenceModel):
    skill_id: str = Field(min_length=1, max_length=40, pattern=r"^[a-z0-9_]+$")
    label: str = Field(min_length=1, max_length=80)
    category: SkillCategory
    aliases: list[str] = Field(min_length=1, max_length=20)


class MarketRole(EvidenceModel):
    role_id: MarketRoleID
    label: str = Field(min_length=1, max_length=80)
    description: str = Field(min_length=1, max_length=300)


class MarketCatalog(EvidenceModel):
    version: str = Field(min_length=1, max_length=40)
    roles: list[MarketRole]
    skills: list[MarketSkill]


class MarketPosting(EvidenceModel):
    source: str = Field(min_length=1, max_length=50, pattern=r"^[a-z0-9_.-]+$")
    external_id: str = Field(min_length=1, max_length=255)
    source_url: HttpUrl
    company: str = Field(min_length=1, max_length=200)
    title: str = Field(min_length=1, max_length=255)
    experience_min_years: int | None = Field(default=None, ge=0, le=60, strict=True)
    experience_max_years: int | None = Field(default=None, ge=0, le=60, strict=True)
    captured_at: AwareDatetime
    closes_at: AwareDatetime | None = None
    tech_stack: list[str] = Field(default_factory=list, max_length=80)
    responsibilities: str = Field(default="", max_length=MAX_SECTION_CHARS)
    qualifications: str = Field(default="", max_length=MAX_SECTION_CHARS)
    preferred: str = Field(default="", max_length=MAX_SECTION_CHARS)

    @model_validator(mode="after")
    def validate_posting(self):
        if (
            self.source_url.scheme != "https"
            or self.source_url.username
            or self.source_url.password
        ):
            raise ValueError("Source URL must be HTTPS without credentials")
        if (
            self.experience_min_years is not None
            and self.experience_max_years is not None
            and self.experience_max_years < self.experience_min_years
        ):
            raise ValueError("Experience range is reversed")
        if not (self.tech_stack or self.responsibilities or self.qualifications or self.preferred):
            raise ValueError("A posting needs at least one section")
        return self

    @field_validator("tech_stack")
    @classmethod
    def stack_items(cls, value):
        if any(not item.strip() or len(item) > 80 or "\x00" in item for item in value):
            raise ValueError("Tech stack items must be 1-80 characters")
        return [item.strip() for item in value]

    @field_validator(
        "company", "title", "external_id", "responsibilities", "qualifications", "preferred"
    )
    @classmethod
    def storable_text(cls, value):
        if "\x00" in value:
            raise ValueError("NUL characters cannot be preserved in JSONB")
        value.encode("utf-8")
        return value


class MarketSnapshotRequest(EvidenceModel):
    role_id: MarketRoleID
    as_of: AwareDatetime
    postings: list[MarketPosting] = Field(min_length=1, max_length=MAX_POSTINGS)


class SkillStatistic(EvidenceModel):
    rank: int
    skill_id: str
    label: str
    category: SkillCategory
    mentioned_postings: int  # 이 기술을 언급한 공고 수 (공고당 1회)
    required_postings: int
    stack_postings: int
    preferred_postings: int
    mention_rate: float  # mentioned_postings / sample_size
    mention_rate_low: float  # Wilson 95% 구간
    mention_rate_high: float
    required_ratio: float  # required_postings / mentioned_postings
    importance: float  # 0~1. 회사 편중을 줄인 가중 점수 (README·설계 문서의 식)


class SkillPair(EvidenceModel):
    skill_a: str
    skill_b: str
    postings: int
    lift: float


class ExperienceCount(EvidenceModel):
    bucket: ExperienceBucket
    postings: int


class SourceCount(EvidenceModel):
    source: str
    postings: int


class ExtractedSkill(EvidenceModel):
    skill_id: str
    level: MentionLevel


class PostingExtraction(EvidenceModel):
    source: str
    external_id: str
    skills: list[ExtractedSkill]


class MarketSnapshot(EvidenceModel):
    role_id: MarketRoleID
    role_label: str
    catalog_version: str
    extractor_version: str
    as_of: AwareDatetime
    sample_size: int  # 중복을 뺀 공고 수 (모든 비율의 분모)
    duplicates_removed: int
    companies: int
    sources: list[SourceCount]
    experience: list[ExperienceCount]
    skills: list[SkillStatistic]  # importance 내림차순
    pairs: list[SkillPair]
    warnings: list[str]
    content_hash: str  # content_hash를 뺀 나머지의 RFC 8785 SHA-256


class MarketSnapshotOutput(EvidenceModel):
    snapshot: MarketSnapshot
    extractions: list[PostingExtraction]

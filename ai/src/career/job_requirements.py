import re
from datetime import datetime

from src.career.contracts.career import JobPostingInput, RequirementCandidate
from src.career.errors import EvidenceError
from src.career.skill_catalog import bounded_normalized_text, mentioned_skills, normalized_text

# 학습자료 선수지식 검사(learning_retrieval)도 이 패턴을 쓴다. 바꾸면 저장된 공고·자료 버전이
# 재검사(409)에 걸릴 수 있으니 기존 데이터를 먼저 점검한다.
OR_PATTERN = r"\b(or|either)\b|또는|혹은|중\s*(하나|한\s*가지)"
AND_PATTERN = r"\b(and|both)\b|및|모두|(?<=[a-z0-9])(?:와|과)(?=\s)"
EXPERIENCE_PATTERN = r"년|개월|연차|\b(?:years?|months?|yrs?)\b"


def infer_logic_normalized(text: str) -> str:
    """이미 normalized_text를 거친 텍스트용 infer_logic."""
    has_or = bool(re.search(OR_PATTERN, text))
    has_and = bool(re.search(AND_PATTERN, text))
    return "any" if has_or and not has_and else "all" if has_and and not has_or else "unspecified"


def infer_logic(quote: str) -> str:
    return infer_logic_normalized(normalized_text(quote))


def validate_annotations(posting: JobPostingInput, now: datetime):
    if posting.captured_at > now:
        raise EvidenceError("CAPTURE_TIME_IS_IN_THE_FUTURE")
    # 여기서는 결과를 쓰지 않는다. 중복 제거(gap_analyzer.deduplicate)가 요청마다 저장된 버전의
    # 원문을 다시 정규화하므로 그 크기를 반입·재검사 때 묶어 둔다.
    bounded_normalized_text(posting.raw_text)
    for requirement in posting.requirements:
        text = bounded_normalized_text(requirement.quote)  # 인용마다 한 번만 정규화한다.
        mentioned = set(mentioned_skills(text))
        if not mentioned.issuperset(requirement.skill_ids):
            raise EvidenceError("ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE")
        if requirement.kind == "skill" and set(requirement.skill_ids) != mentioned:
            raise EvidenceError("ANNOTATION_DROPS_KNOWN_SKILLS_OR_ALTERNATIVES")
        if len(requirement.skill_ids) > 1 and requirement.match != "unspecified":
            if infer_logic_normalized(text) != requirement.match:
                raise EvidenceError("AMBIGUOUS_OR_INCONSISTENT_REQUIREMENT_LOGIC")


def preview_requirements(raw_text: str) -> list[RequirementCandidate]:
    """Keyword candidates only: never assign mandatory/optional or proficiency."""
    # 반입과 같은 상한. 원문 전체가 통과하면 아래 줄별 정규화 크기도 함께 묶인다.
    bounded_normalized_text(raw_text)
    candidates = []
    offset = 0
    for line in raw_text.splitlines(keepends=True):
        quote = line.rstrip("\r\n")
        text = normalized_text(quote)  # 줄마다 한 번만 정규화한다.
        skills = mentioned_skills(text)
        if skills:
            candidates.append(
                RequirementCandidate(
                    quote=quote,
                    start=offset,
                    end=offset + len(quote),
                    skill_ids=skills,
                    match=infer_logic_normalized(text) if len(skills) > 1 else "all",
                )
            )
        offset += len(line)
    return candidates

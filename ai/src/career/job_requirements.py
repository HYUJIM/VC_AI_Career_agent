import re
from datetime import datetime

from src.career.contracts.career import JobPostingInput, RequirementCandidate
from src.career.errors import EvidenceError
from src.career.skill_catalog import CATALOG, contains_skill, normalized_text

OR_PATTERN = r"\b(or|either)\b|또는|혹은|중\s*(하나|한\s*가지)"
AND_PATTERN = r"\b(and|both)\b|및|모두|(?<=[a-z0-9])(?:와|과)(?=\s)"
EXPERIENCE_PATTERN = r"년|개월|연차|\b(?:years?|months?|yrs?)\b"


def infer_logic(quote: str) -> str:
    text = normalized_text(quote)
    has_or = bool(re.search(OR_PATTERN, text))
    has_and = bool(re.search(AND_PATTERN, text))
    return "any" if has_or and not has_and else "all" if has_and and not has_or else "unspecified"


def validate_annotations(posting: JobPostingInput, now: datetime):
    if posting.captured_at > now:
        raise EvidenceError("CAPTURE_TIME_IS_IN_THE_FUTURE")
    for requirement in posting.requirements:
        mentioned = {
            skill.skill_id
            for skill in CATALOG.skills
            if contains_skill(requirement.quote, skill.skill_id)
        }
        if any(not contains_skill(requirement.quote, value) for value in requirement.skill_ids):
            raise EvidenceError("ANNOTATED_SKILL_NOT_MENTIONED_IN_QUOTE")
        if requirement.kind == "skill" and set(requirement.skill_ids) != mentioned:
            raise EvidenceError("ANNOTATION_DROPS_KNOWN_SKILLS_OR_ALTERNATIVES")
        if len(requirement.skill_ids) > 1 and requirement.match != "unspecified":
            if infer_logic(requirement.quote) != requirement.match:
                raise EvidenceError("AMBIGUOUS_OR_INCONSISTENT_REQUIREMENT_LOGIC")


def preview_requirements(raw_text: str) -> list[RequirementCandidate]:
    """Keyword candidates only: never assign mandatory/optional or proficiency."""
    candidates = []
    offset = 0
    for line in raw_text.splitlines(keepends=True):
        quote = line.rstrip("\r\n")
        skills = [
            skill.skill_id for skill in CATALOG.skills if contains_skill(quote, skill.skill_id)
        ]
        if skills:
            candidates.append(
                RequirementCandidate(
                    quote=quote,
                    start=offset,
                    end=offset + len(quote),
                    skill_ids=skills,
                    match=infer_logic(quote) if len(skills) > 1 else "all",
                )
            )
        offset += len(line)
    return candidates

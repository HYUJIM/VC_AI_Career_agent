import re
from uuid import NAMESPACE_URL, uuid5

from src.career.contracts.career import MAPPING_VERSION, EvidenceFragment, SkillEvidence
from src.career.contracts.verification import VerificationSnapshot
from src.career.skill_catalog import CATALOG, exact_skill, normalized_text

# A rule reports only what the exact issuer claim says; it is not a proficiency
# taxonomy. Descriptions, badge titles and alignment are otherwise opaque data.
SUFFIX_RULES = {
    "평가 통과": "assessment",
    "과정 수료": "completion",
    "프로젝트 완료": "project",
    "실무 경험 확인": "experience",
}
POINTER = "/credentialSubject/achievement"


def map_skills(snapshot: VerificationSnapshot) -> tuple[list[SkillEvidence], list]:
    evidence = []
    unmapped = []
    for outcome in snapshot.accepted_credentials:
        if not outcome.accepted or outcome.status != "verified" or outcome.credential is None:
            continue
        credential = outcome.credential
        achievement = credential.achievement
        found = []
        description = achievement.get("description")
        contradictory = isinstance(description, str) and re.search(
            r"미수료|미이수|불합격|\bfailed\b|\bnot (completed|passed)\b",
            normalized_text(description),
        )
        for skill in CATALOG.skills:
            for suffix, evidence_type in SUFFIX_RULES.items():
                if not contradictory and normalized_text(credential.achievement_name) in {
                    normalized_text(f"{alias} {suffix}") for alias in skill.aliases
                }:
                    found.append(
                        (
                            skill.skill_id,
                            evidence_type,
                            f"title-{skill.skill_id}-{evidence_type}",
                            "exact_claim_rule",
                            [
                                EvidenceFragment(
                                    pointer=f"{POINTER}/name", quote=credential.achievement_name
                                )
                            ],
                        )
                    )
        # Existing signed fixture/demo format; both exact fragments are required.
        if (
            credential.achievement_name == "SQL assessment"
            and achievement.get("description") == "SQL exam passed"
        ):
            found.append(
                (
                    "sql",
                    "assessment",
                    "sql-assessment-passed",
                    "exact_claim_rule",
                    [
                        EvidenceFragment(
                            pointer=f"{POINTER}/name", quote=credential.achievement_name
                        ),
                        EvidenceFragment(pointer=f"{POINTER}/description", quote="SQL exam passed"),
                    ],
                )
            )
        alignments = achievement.get("alignment")
        if isinstance(alignments, list):
            for index, alignment in enumerate(alignments):
                if not isinstance(alignment, dict) or not isinstance(
                    alignment.get("targetName"), str
                ):
                    continue
                skill_id = exact_skill(alignment["targetName"])
                if skill_id:
                    found.append(
                        (
                            skill_id,
                            "alignment",
                            f"alignment-{index}",
                            "explicit_alignment",
                            [
                                EvidenceFragment(
                                    pointer=f"{POINTER}/alignment/{index}/targetName",
                                    quote=alignment["targetName"],
                                )
                            ],
                        )
                    )
        if not found:
            unmapped.append(outcome.record_id)
        for skill_id, kind, rule, interpretation, fragments in found:
            evidence.append(
                SkillEvidence(
                    evidence_id=uuid5(
                        NAMESPACE_URL,
                        f"{snapshot.verification_snapshot_id}:{outcome.record_id}:{MAPPING_VERSION}:{rule}",
                    ),
                    skill_id=skill_id,
                    record_id=outcome.record_id,
                    verification_snapshot_id=snapshot.verification_snapshot_id,
                    evidence_type=kind,
                    rule_id=rule,
                    interpretation=interpretation,
                    source_fragments=fragments,
                )
            )
    return evidence, unmapped

from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from src.career.contracts.curriculum import (
    PLANNER_VERSION,
    CurriculumDraft,
    DraftStep,
    PlanningContext,
    PlanningQuestion,
    PlanOption,
)
from src.career.learning_retrieval import RESOURCE_FRESHNESS_DAYS, retrieve_resources
from src.career.output_validator import validate_draft
from src.career.errors import EvidenceError


def comparison_ref(comparison):
    return f"{comparison.version_id}:{comparison.requirement.key}"


def build_context(analysis, resources, request, now):
    if len(analysis.comparisons) > 200:
        raise EvidenceError("SELECT_TARGET_POSTING_OR_NARROW_COMPARISON")
    alternatives = {item.comparison_ref: item.skill_id for item in request.alternative_choices}
    actions = {item.skill_id: item.action for item in request.learning_choices}
    roots, questions, warnings, used_alternatives = {}, [], [], set()
    for comparison in analysis.comparisons:
        requirement, ref = comparison.requirement, comparison_ref(comparison)
        if comparison.status == "supported":
            continue
        if comparison.status == "unknown" or requirement.kind != "skill":
            questions.append(
                PlanningQuestion(
                    comparison_ref=ref,
                    code="MANUAL_ASSESSMENT_REQUIRED",
                    skill_options=requirement.skill_ids,
                )
            )
            continue
        selected = requirement.skill_ids
        if requirement.match == "any" and len(selected) > 1:
            choice = alternatives.get(ref)
            if choice is None:
                questions.append(
                    PlanningQuestion(
                        comparison_ref=ref,
                        code="SELECT_ONE_ALTERNATIVE",
                        skill_options=selected,
                    )
                )
                continue
            if choice not in selected:
                raise EvidenceError("INVALID_ALTERNATIVE_CHOICE")
            used_alternatives.add(ref)
            selected = [choice]
        for skill_id in selected:
            if any(
                item.skill_id == skill_id
                and (
                    item.evidence_type == requirement.minimum_evidence
                    or requirement.minimum_evidence == "alignment"
                )
                for item in analysis.skill_evidence
            ):
                continue
            entry = roots.setdefault(skill_id, {"refs": [], "required": False})
            entry["refs"].append(ref)
            entry["required"] |= requirement.importance == "required"
            if requirement.minimum_evidence == "experience":
                warnings.append("LEARNING_PROJECTS_DO_NOT_PROVE_PROFESSIONAL_EXPERIENCE")
    if set(alternatives) != used_alternatives:
        raise EvidenceError("UNUSED_OR_UNKNOWN_ALTERNATIVE_CHOICE")
    if not set(actions).issubset(roots):
        raise EvidenceError("LEARNING_CHOICE_IS_OUTSIDE_ACTIONABLE_GAPS")
    options, baseline = {}, []

    def step_id(skill, kind):
        return uuid5(NAMESPACE_URL, f"{analysis.analysis_id}:{PLANNER_VERSION}:{skill}:{kind}")

    def expand(skill, refs, graph, path, prerequisite=False):
        if skill in path:
            raise EvidenceError("CYCLIC_RESOURCE_PREREQUISITES")
        action = "assess" if prerequisite else actions.get(skill, "assess")
        kind = "collect_evidence" if action == "collect_evidence" else "practice"
        identity = step_id(skill, kind)
        if identity in graph:
            graph[identity].comparison_refs = sorted(set(graph[identity].comparison_refs + refs))
            if not prerequisite:
                graph[identity].purpose = "gap_followup"
                if action == "learn":
                    graph[identity].conditional = False
            return identity
        purpose = "resource_prerequisite" if prerequisite else "gap_followup"
        if action == "collect_evidence":
            graph[identity] = PlanOption(
                step_id=identity,
                skill_id=skill,
                kind=kind,
                comparison_refs=refs,
                depends_on=[],
                resource_options=[],
                minimum_hours=1,
                conditional=False,
                purpose=purpose,
            )
            return identity
        candidates = retrieve_resources(resources, skill, request.budget_krw, now)
        selected = candidates[0] if candidates else None
        prerequisite_ids = (
            [item.skill_id for item in selected.resource.prerequisite_annotations]
            if selected
            else []
        )
        dependencies = [
            expand(value, refs, graph, path | {skill}, True) for value in prerequisite_ids
        ]
        if action == "assess":
            diagnostic = step_id(skill, "self_assessment")
            if diagnostic not in graph:
                graph[diagnostic] = PlanOption(
                    step_id=diagnostic,
                    skill_id=skill,
                    kind="self_assessment",
                    comparison_refs=refs,
                    depends_on=dependencies,
                    resource_options=[],
                    minimum_hours=1,
                    conditional=False,
                    purpose=purpose,
                )
            dependencies = [diagnostic]
        matching = [
            candidate
            for candidate in candidates
            if {item.skill_id for item in candidate.resource.prerequisite_annotations}
            == set(prerequisite_ids)
        ][:3]
        graph[identity] = PlanOption(
            step_id=identity,
            skill_id=skill,
            kind="practice",
            comparison_refs=refs,
            depends_on=dependencies,
            resource_options=matching,
            minimum_hours=selected.resource.minimum_study_hours if selected else 3,
            conditional=action == "assess",
            purpose=purpose,
        )
        return identity

    for skill in sorted(roots, key=lambda value: (not roots[value]["required"], value)):
        tentative = {key: value.model_copy(deep=True) for key, value in options.items()}
        try:
            expand(skill, sorted(set(roots[skill]["refs"])), tentative, set())
            proposed = CurriculumDraft(
                steps=[
                    DraftStep(
                        step_id=option.step_id,
                        hours=option.minimum_hours,
                        resource_version_id=(
                            option.resource_options[0].version_id
                            if option.resource_options
                            else None
                        ),
                    )
                    for option in tentative.values()
                ]
            )
            provisional = PlanningContext(
                options=list(tentative.values()),
                baseline=proposed,
                questions=[],
                warnings=[],
            )
            errors = validate_draft(proposed, provisional, request)
            if errors:
                raise EvidenceError(errors[0])
            options, baseline = tentative, proposed.steps
        except EvidenceError as exc:
            questions.extend(
                PlanningQuestion(
                    comparison_ref=ref,
                    code=str(exc),
                    skill_options=[skill],
                )
                for ref in roots[skill]["refs"]
            )
    for option in options.values():
        if option.kind == "practice" and not option.resource_options:
            warnings.append("GENERAL_EXERCISE_NO_REVIEWED_COURSE")
        if option.kind == "self_assessment":
            warnings.append("CONFIRM_EXPERIENCE_BEFORE_CONDITIONAL_LEARNING")
    if not roots and not questions:
        warnings.append("NO_ACTIONABLE_LEARNING_GAPS_IN_THIS_ANALYSIS")
    limits = [analysis.valid_until]
    limits.extend(
        version.resource.captured_at + timedelta(days=RESOURCE_FRESHNESS_DAYS)
        for option in options.values()
        for version in option.resource_options
    )
    return PlanningContext(
        options=list(options.values()),
        baseline=CurriculumDraft(steps=baseline),
        questions=questions,
        warnings=sorted(set(warnings)),
        valid_until=min(limits),
    )

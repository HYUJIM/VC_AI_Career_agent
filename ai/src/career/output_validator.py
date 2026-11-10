from src.career.contracts.curriculum import (
    CurriculumDraft,
    CurriculumStep,
    PlanningContext,
    WeekAllocation,
)
from src.career.skill_catalog import CATALOG


def validate_draft(draft: CurriculumDraft, context: PlanningContext, request) -> list[str]:
    errors = []
    options = {option.step_id: option for option in context.options}
    ids = [step.step_id for step in draft.steps]
    if len(ids) != len(set(ids)) or set(ids) != set(options):
        return ["STEP_SET_MISMATCH"]
    seen, paid = set(), {}
    total = 0
    for step in draft.steps:
        option = options[step.step_id]
        if not set(option.depends_on).issubset(seen):
            errors.append("PREREQUISITE_ORDER")
        resources = {item.version_id: item for item in option.resource_options}
        selected = resources.get(step.resource_version_id)
        if resources and selected is None or not resources and step.resource_version_id is not None:
            errors.append("UNRETRIEVED_RESOURCE")
        minimum = selected.resource.minimum_study_hours if selected else option.minimum_hours
        if step.hours < minimum:
            errors.append("INSUFFICIENT_STEP_HOURS")
        if selected:
            paid[selected.version_id] = selected.resource.cost_krw
        total += step.hours
        seen.add(step.step_id)
    if total > request.weeks * request.hours_per_week:
        errors.append("TIME_BUDGET_EXCEEDED")
    if request.budget_krw is not None:
        if any(value is None for value in paid.values()):
            errors.append("UNKNOWN_PRICE_WITH_STRICT_BUDGET")
        elif sum(paid.values()) > request.budget_krw:
            errors.append("COST_BUDGET_EXCEEDED")
    return sorted(set(errors))


def render_steps(draft, context, request):
    """Only server templates and retrieved source metadata become public prose."""
    options = {item.step_id: item for item in context.options}
    labels = {skill.skill_id: skill.label for skill in CATALOG.skills}
    result, week, used, prices = [], 1, 0, {}
    for selected in draft.steps:
        option = options[selected.step_id]
        resource = next(
            (
                item
                for item in option.resource_options
                if item.version_id == selected.resource_version_id
            ),
            None,
        )
        if resource:
            prices[resource.version_id] = resource.resource.cost_krw
        remaining, allocations = selected.hours, []
        while remaining:
            capacity = request.hours_per_week - used
            if capacity == 0:
                week, used, capacity = week + 1, 0, request.hours_per_week
            hours = min(remaining, capacity)
            allocations.append(WeekAllocation(week=week, hours=hours))
            remaining, used = remaining - hours, used + hours
        label = labels[option.skill_id]
        if option.kind == "self_assessment":
            goal = f"{label} 현재 경험 확인 (자가진단 제안)"
            activity = (
                "기존 학습·프로젝트·업무 경험을 정리하고 작은 로컬 예제로 이해 범위를 확인합니다."
            )
            criterion = "확인된 경험과 아직 증빙되지 않은 범위를 구분하여 기록합니다."
        elif option.kind == "collect_evidence":
            goal = f"{label} 관련 증빙 정리 (계획 제안)"
            activity = "이미 있는 자격증명·프로젝트·평가 자료의 출처와 범위를 정리합니다."
            criterion = "검증 가능한 원본 또는 추가 확인이 필요한 항목 목록을 준비합니다."
        else:
            goal = f"{label} 기초 실습 (학습 계획 제안)"
            activity = (
                "인용된 검토 자료를 참고해 작은 로컬 예제를 구현하고 실행 결과와 한계를 기록합니다."
                if resource
                else "특정 강의를 뜻하지 않는 일반 과제입니다. 작은 로컬 예제를 구현하고 결과를 기록합니다."
            )
            criterion = "재현 절차·결과물·한계 설명을 제출합니다. 수료·숙련·실무 경력 인증을 뜻하지 않습니다."
        result.append(
            CurriculumStep(
                step_id=option.step_id,
                skill_id=option.skill_id,
                kind=option.kind,
                comparison_refs=option.comparison_refs,
                depends_on=option.depends_on,
                conditional=option.conditional,
                purpose=option.purpose,
                hours=selected.hours,
                allocations=allocations,
                goal=goal,
                activity=activity,
                completion_criterion=criterion,
                resource=resource,
            )
        )
    return (
        result,
        sum(value or 0 for value in prices.values()),
        any(value is None for value in prices.values()),
    )

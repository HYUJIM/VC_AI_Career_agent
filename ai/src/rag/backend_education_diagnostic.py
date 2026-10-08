"""Manually scoped fixture diagnostic, not automatic education retrieval or VC verification."""
VERSION = 'be-education-three-records-v1'
ROWS = [
    ('532baf02-c738-45ca-a1de-2d4442c8b04c', '우아한테크코스 6기 수료',
     '웹 백엔드 아키텍처 및 클린코드 과정 수료', '시간 미제공'),
    ('2dff4dd7-2a03-4136-ab98-c5c048b4b179', '클라우드 아키텍처 부트캠프 수료',
     'AWS 클라우드 인프라 구축 과정 수료', '시간 미제공'),
    ('e353fcff-d264-4477-9937-b502c505c55e', '백엔드 개발자 부트캠프 수료',
     '6개월 백엔드 전문 교육 과정 수료', '시간 미제공; 6개월 과정'),
]
QUESTION = (
    '선택된 교육 수료 기록 3건을 각각 한 줄로 답하세요. 형식: 교육명 | 이수 시간 | record_id. '
    '교육명과 record_id는 원문 그대로 쓰세요. 시간 단위 이수량이 없으면 시간 미제공이라고 쓰세요. '
    '개월 단위 과정 기간만 있으면 시간 미제공; N개월 과정이라고 쓰세요. '
    'N은 해당 기록에 명시된 숫자만 사용하세요. 기간을 추측하거나 시간으로 환산하지 마세요. '
    '머리말, JSON, 코드 블록 없이 정확히 3줄만 쓰세요.'
)


def reference(export):
    if export['subject_did'] != 'did:key:z6MkMockTestUser0013988b874':
        raise ValueError('Diagnostic subject does not match reviewed fixture')
    by_id = {r['record_id']: r for r in export['records']}
    result = []
    for rid, name, description, duration in ROWS:
        record = by_id.get(rid)
        if record is None or record['achievement'].get('name') != name or record['achievement'].get('description') != description:
            raise ValueError('Diagnostic source changed; manual review required: ' + rid)
        result.append(dict(record_id=rid, name=name, source_description=description,
                           expected_duration=duration, duration_basis='achievement.description',
                           selection_basis='manually reviewed education completion record'))
    return result


def review(answer, expected, finish_reason):
    """Strict output-contract check; does not rewrite the answer or infer equivalence."""
    errors, seen = [], set()
    by_id = {r['record_id']: r for r in expected}
    lines = [line.strip() for line in answer.splitlines() if line.strip()]
    if finish_reason != 'eos':
        errors.append(dict(code='incomplete_generation', actual=finish_reason))
    if len(lines) != len(expected):
        errors.append(dict(code='line_count', actual=len(lines), expected=len(expected)))
    for number, line in enumerate(lines, 1):
        cells = [c.strip() for c in line.split('|')]
        if len(cells) != 3:
            errors.append(dict(code='format', line=number)); continue
        name, duration, rid = cells
        if rid not in by_id:
            errors.append(dict(code='unknown_or_unselected_id', line=number, actual=rid)); continue
        if rid in seen:
            errors.append(dict(code='duplicate_id', line=number, actual=rid))
        seen.add(rid)
        row = by_id[rid]
        for field, actual, target in [('name', name, row['name']), ('duration', duration, row['expected_duration'])]:
            if actual != target:
                errors.append(dict(code=field + '_mismatch', line=number, record_id=rid,
                                   actual=actual, expected=target))
    for rid in by_id.keys() - seen:
        errors.append(dict(code='missing_id', record_id=rid))
    return dict(status='failed' if errors else 'passed', errors=errors, version=VERSION,
                scope='strict fixture extraction contract; not general factuality or VC verification',
                answer_modified=False, automatic_retrieval_evaluated=False)

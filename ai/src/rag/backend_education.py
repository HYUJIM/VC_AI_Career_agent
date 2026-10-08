"""Conservative BE education v1. Finite grammar, no VC or general NLP claims."""
import re
from copy import deepcopy

from src.data.backend_adapter import validate_backend_export, json_hash

VERSION = 'be-education-structure-v1'
EDU = r'교육|과정|부트캠프|아카데미|캠프|테크코스'
DONE = r'수료|이수'
NON_EDU = r'기사|자격증|TOEIC|OPIc|Certified|Professional|수상|우수상|대상|메달|Medal|기여 인증'
UNCERTAIN = r'예정|계획|미수료|미이수|수료하지|이수하지|수료 안|이수 안|미완료'
NUM = r'(?<![\d.])\d+(?:\.\d+)?'
UNITS = {'course_duration': '개월|주|일|년', 'completion_hours': '시간'}


def evidence(path, text, match):
    return dict(path=path, quote=match.group(), start=match.start(), end=match.end())


def texts(record):
    achievement = record['achievement']
    return [('achievement.' + key, achievement[key]) for key in
            ('name', 'description', 'criteria_narrative', 'achievement_type') if key in achievement]


def classify(record):
    fields = texts(record)
    combined = '\n'.join(value for _, value in fields)
    positive = bool(re.search(EDU, combined) and re.search(DONE, combined))
    negative = bool(re.search(NON_EDU, combined, re.I))
    uncertain = bool(re.search(UNCERTAIN, combined))
    state = 'review' if uncertain or positive and negative else 'selected' if positive else 'rejected' if negative else 'review'
    quotes = [evidence(path, value, m) for path, value in fields
              for m in re.finditer(EDU + '|' + DONE + '|' + NON_EDU + '|' + UNCERTAIN, value, re.I)]
    return dict(status=state, method='finite_korean_rules_v1', evidence=quotes,
                reason='conflicting_or_uncertain_signals' if uncertain or positive and negative else
                'explicit_education_completion' if positive else 'explicit_non_target' if negative else 'insufficient_signals')


def extract_field(record, field):
    # No dates, title editions (6기), backend status or learned world knowledge.
    pattern = NUM + r'\s*(' + UNITS[field] + r')'
    found, all_evidence, uncertain = [], [], False
    corpus = '\n'.join(t for p, t in texts(record) if not p.endswith('achievement_type'))
    for path, value in texts(record):
        if path.endswith('achievement_type'):
            continue
        for match in re.finditer(pattern, value):
            quote = evidence(path, value, match)
            all_evidence.append(quote)
            # Use sentence boundaries without splitting decimal numbers.
            clauses = re.split(r'(?<!\d)[.!?\n]|[.!?](?!\d)', value)
            clause = next((s for s in clauses if match.group() in s), value)
            uncertain |= bool(re.search(UNCERTAIN + r'|주당|매주|일일|하루|매일|경력|경험|이상|이하|약\s*\d|~|∼|부터|까지', clause))
            uncertain |= not bool(re.search(EDU if field == 'course_duration' else DONE, clause))
            found.append((float(re.search(NUM, match.group()).group()), match.group(1)))
    relevant = r'개월|(?<![가-힣])달|주간|일간|년간' if field == 'course_duration' else r'시간|\d+\s*분'
    if not found:
        unparsed = [(p, t, m) for p, t in texts(record) for m in re.finditer(relevant, t)]
        status = 'unassessable' if unparsed else 'not_provided'
        return dict(value=None, unit=None, status=status, method='finite_korean_rules_v1',
                    evidence=[evidence(p, t, m) for p, t, m in unparsed])
    different = set(found)
    status = 'ambiguous' if uncertain else 'conflicting' if len(different) > 1 else 'provided'
    # Mixed supported and unsupported mentions must not silently discard the latter.
    if field == 'completion_hours' and re.search(r'\d+\s*분', corpus):
        status = 'unassessable'
    value, unit = found[0] if status == 'provided' else (None, None)
    if value is not None and value.is_integer():
        value = int(value)
    return dict(value=value, unit=unit, status=status, evidence=all_evidence, method='finite_korean_rules_v1')


def structure(export):
    validate_backend_export(export)
    rows = []
    for record in export['records']:
        rows.append(dict(record_id=record['record_id'], subject_did=record['subject_did'],
            name=record['achievement']['name'], source_achievement=deepcopy(record['achievement']),
            backend_status=record['backend_status'], source_record_sha256=json_hash(record),
            raw_data_sha256=record['provenance']['raw_data_sha256'], selection=classify(record),
            course_duration=extract_field(record, 'course_duration'),
            completion_hours=extract_field(record, 'completion_hours')))
    return dict(version=VERSION, source_export_sha256=json_hash(export),
                source_scope='backend_export.achievement name/description/criteria_narrative/achievement_type only',
                paper_evaluation_eligible=False, cryptographic_verification_performed=False, records=rows)


def display_value(field, kind):
    if field['status'] == 'provided':
        return str(field['value']) + field['unit']
    if field['status'] == 'not_provided':
        return '기간 미제공' if kind == 'course_duration' else '시간 미제공'
    return '확인 필요'


def source_display(result):
    return [dict(record_id=r['record_id'], name=r['name'], selection=r['selection']['status'],
                 course_duration=display_value(r['course_duration'], 'course_duration'),
                 completion_hours=display_value(r['completion_hours'], 'completion_hours'))
            for r in result['records']]


def selection_metrics(rows, expected):
    actual = {r['record_id']: r['selection']['status'] for r in rows}
    gold = {r['record_id']: r['selection'] for r in expected}
    selected = {rid for rid, state in actual.items() if state == 'selected'}
    target = {rid for rid, state in gold.items() if state == 'selected'}
    tp, fp, fn = len(selected & target), len(selected - target), len(target - selected)
    return dict(tp=tp, fp=fp, fn=fn, precision=tp/(tp+fp) if tp+fp else None,
                recall=tp/(tp+fn) if tp+fn else None, review_count=sum(v == 'review' for v in actual.values()),
                exact_states=actual == gold, predicted=actual, expected=gold)


def field_metrics(rows, expected):
    by_id = {r['record_id']: r for r in rows}
    mismatches = []
    for row in expected:
        for field in UNITS:
            for key in ('status', 'value', 'unit'):
                if by_id[row['record_id']][field][key] != row[field][key]:
                    mismatches.append(dict(record_id=row['record_id'], field=field, key=key,
                        expected=row[field][key], actual=by_id[row['record_id']][field][key]))
    return dict(status='failed' if mismatches else 'passed', mismatches=mismatches)


QUESTION = ('명시된 교육 수료 기록을 각각 한 줄로 쓰세요. '
    '형식: 교육명 | 과정 기간 | 이수 시간 | record_id. 이름과 ID는 원문 그대로 쓰세요. '
    '과정 기간과 실제 이수 시간을 분리하고 추측하거나 환산하지 마세요. '
    '정보가 없으면 각각 기간 미제공, 시간 미제공이라고 쓰세요. '
    '상충하거나 불명확하면 확인 필요라고 쓰세요. 머리말이나 코드 블록 없이 답하세요.')


def generation_prompt(export, ids):
    from src.rag.backend_pipeline import prepare_backend_prompt
    return prepare_backend_prompt(export, QUESTION, record_ids=ids)['prompt']

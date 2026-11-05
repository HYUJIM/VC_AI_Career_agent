"""Separated finite-contract answer review; unknown is never factual success."""
import re
from src.rag.backend_education import display_value

VERSION = 'be-education-answer-evaluation-v1'


def evaluate_answer(answer, expected, finish_reason):
    axes = dict(facts=[], wording=[], unknown=[], completeness=[])
    target = {r['record_id']: r for r in expected if r['selection'] == 'selected'}
    seen = set()
    def add(axis, code, **details):
        axes[axis].append(dict(code=code, **details))
    if finish_reason != 'eos':
        add('completeness', 'incomplete_generation', actual=finish_reason)
        add('unknown', 'truncated_or_unconfirmed_completion')
    for line_num, line in enumerate(answer.splitlines(), 1):
        if not line.strip():
            continue
        cells = [c.strip() for c in line.split('|')]
        if len(cells) != 4:
            add('wording', 'format', line=line_num)
            add('unknown', 'unparsed_claims', line=line_num, raw=line)
            continue
        name, duration, hours, rid = cells
        if rid not in target:
            add('facts', 'unknown_or_non_target_id', line=line_num, actual=rid)
            add('unknown', 'unbound_claims', line=line_num)
            continue
        if rid in seen:
            add('completeness', 'duplicate_id', record_id=rid)
        seen.add(rid)
        source = target[rid]
        if name != source['name']:
            add('wording', 'source_name_mismatch', record_id=rid, actual=name, expected=source['name'])
        for field, actual in [('course_duration', duration), ('completion_hours', hours)]:
            gold = source[field]
            correct = display_value(gold, field)
            if gold['status'] not in ('provided', 'not_provided'):
                add('unknown', 'source_requires_review', record_id=rid, field=field, actual=actual)
                if actual != correct:
                    add('wording', 'review_marker_required', record_id=rid, field=field)
            elif actual == correct:
                continue
            elif actual in ('N/A', '확인 필요', '미상', '알 수 없음'):
                add('unknown', 'ambiguous_or_abstained', record_id=rid, field=field, actual=actual)
            elif actual in ('기간 미제공', '시간 미제공'):
                add('completeness', 'provided_value_omitted' if gold['status'] == 'provided' else 'wrong_missing_marker',
                    record_id=rid, field=field, actual=actual)
            else:
                number = re.fullmatch(r'(\d+(?:\.\d+)?)\s*(개월|주|일|년|시간|분)', actual)
                if number and (gold['status'] == 'not_provided' or float(number[1]) != gold['value'] or number[2] != gold['unit']):
                    add('facts', 'unsupported_numeric_claim', record_id=rid, field=field, actual=actual, expected=gold)
                elif number:
                    add('wording', 'noncanonical_numeric_expression', record_id=rid, field=field, actual=actual)
                else:
                    add('unknown', 'unrecognized_expression', record_id=rid, field=field, actual=actual)
    for rid in sorted(set(target) - seen):
        add('completeness', 'missing_id', record_id=rid)
    status = 'failed' if axes['facts'] or axes['completeness'] else 'needs_review' if axes['wording'] or axes['unknown'] else 'passed'
    return dict(version=VERSION, status=status, axes=axes, answer_modified=False,
        expected_selected_ids=list(target), seen_ids=sorted(seen),
        paper_evaluation_eligible=False, scope='finite output contract; unknown claims require human review')

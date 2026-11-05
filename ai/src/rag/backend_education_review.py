"""Versioned, conservative review of the manually selected three-record fixture."""
import re
from src.rag.backend_education_diagnostic import reference, review

VERSION = 'be-education-review-v2'


def evaluate(answer, export, finish_reason):
    expected = reference(export)  # Reject unreviewed source changes.
    strict = review(answer, expected, finish_reason)
    identity_codes = {'line_count', 'format', 'unknown_or_unselected_id', 'duplicate_id', 'missing_id', 'incomplete_generation'}
    identity_errors = [e for e in strict['errors'] if e['code'] in identity_codes]
    fields = []
    by_id = {r['record_id']: r for r in expected}
    for row in expected:
        # These values describe this reviewed fixture only, not a general extractor.
        row['course_duration'] = {'value': 6, 'unit': 'months', 'source_quote': '6개월 백엔드 전문 교육 과정 수료'} if row['source_description'].startswith('6개월 ') else None
        row['completion_hours'] = None
        row['completion_hours_status'] = 'not_provided'
        row['course_duration_status'] = 'provided' if row['course_duration'] else 'not_provided'
    for number, line in enumerate(answer.splitlines(), 1):
        if not line.strip(): continue
        cells = [x.strip() for x in line.split('|')]
        if len(cells) != 3 or cells[2] not in by_id:
            fields.append({'line': number, 'status': 'unassessable', 'reason': 'invalid_structure_or_id'})
            continue
        name, duration, rid = cells
        source = by_id[rid]
        supported = source['course_duration'] is not None
        if duration == source['expected_duration']:
            state, reason = 'supported', 'explicit_missing_hours_and_source_duration'
        elif duration == '6개월' and supported:
            state, reason = 'supported', 'source_course_duration_only_hours_not_explicit'
        elif duration == 'N/A':
            state, reason = 'ambiguous', 'not_available_vs_not_applicable'
        elif duration == '시간 미제공':
            state, reason = 'supported', 'hours_not_provided_course_duration_omitted'
        elif re.search(r'\d+\s*(개월|년|시간|분|일|주|달)', duration):
            state, reason = 'unsupported', 'numeric_duration_not_supported_by_this_record_or_unrecognized_expression'
        else:
            state, reason = 'unassessable', 'unrecognized_expression_requires_manual_review'
        fields.append(dict(line=number, record_id=rid, original_duration=duration,
                           original_name=name, status=state, reason=reason))
    states = {r['status'] for r in fields}
    duration_status = ('failed' if 'unsupported' in states else 'needs_review'
                       if identity_errors or states & {'ambiguous', 'unassessable'} else 'passed')
    return dict(version=VERSION, scope='manually reviewed three-record fixture only',
                answer_modified=False, inference_performed_by_review=False,
                paper_evaluation_eligible=False, automatic_retrieval_evaluated=False,
                overall_status='failed' if identity_errors or 'unsupported' in states else
                    'needs_review' if strict['errors'] or duration_status != 'passed' else 'passed',
                identity_and_completeness={'status': 'failed' if identity_errors else 'passed', 'errors': identity_errors},
                duration_grounding={'status': duration_status, 'claims': fields},
                wording_and_format={'status': strict['status'], 'errors': strict['errors']},
                source_fields=expected)

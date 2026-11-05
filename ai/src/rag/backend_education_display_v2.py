"""Presentation v2 over frozen v1 extraction; no model or human approval."""
from copy import deepcopy
from html import escape
import json

from src.rag.backend_education import structure

LABELS = {
    'provided': '원문 명시 · 사람 검토 대기',
    'not_provided': '미제공 (검사한 원문 범위)',
    'ambiguous': '확인 필요 · 의미 모호',
    'conflicting': '확인 필요 · 근거 상충',
    'unassessable': '확인 필요 · 지원 밖 표현',
}
SELECTION = {'selected': '교육 수료 후보', 'review': '선별 보류', 'rejected': '대상 제외'}


def build_display(export):
    """Use the existing validated export; do not interpret proposed BE v2 fields."""
    result = structure(export)
    records = deepcopy(result['records'])
    for row in records:
        row['selection']['label'] = SELECTION[row['selection']['status']]
        row['human_review'] = {'status': 'pending', 'reviewer': None, 'reviewed_at': None}
        for key in ('course_duration', 'completion_hours'):
            field = row[key]
            field['label'] = LABELS[field['status']]
            field['display'] = (str(field['value']) + field['unit']
                                if field['status'] == 'provided' else field['label'])
    return {
        'schema_version': 'be-education-display/2.0',
        'contract_status': 'proposed_pending',
        'integration_status': 'standalone_example_not_product_connected',
        'structure_version': result['version'],
        'source_scope': result['source_scope'],
        'source_export_sha256': result['source_export_sha256'],
        'cryptographic_verification_performed': False,
        'paper_evaluation_eligible': False,
        'source_display': records,
        'model_answer': {'status': 'not_run', 'text': None},
        'review_result': {'status': 'pending', 'human_adjudication': False},
    }


def render_html(response):
    """All source and model text is escaped. No external assets or JavaScript."""
    def e(value):
        return escape(str(value), quote=True)

    rows = []
    for row in response['source_display']:
        fields = ''.join('<td>' + e(row[k]['display']) + '<br><small>' +
                         e(row[k]['label']) + '</small></td>'
                         for k in ('course_duration', 'completion_hours'))
        details = json.dumps({k: row[k] for k in (
            'subject_did', 'source_record_sha256', 'raw_data_sha256',
            'source_achievement', 'selection', 'course_duration', 'completion_hours')},
            ensure_ascii=False, indent=2)
        rows.append('<tr><td>' + e(row['name']) + '<br><code>' + e(row['record_id']) +
                    '</code></td><td>' + e(row['selection']['label']) + '</td>' + fields +
                    '<td>사람 검토 대기<br>BE 보고 상태: ' + e(row['backend_status']) +
                    '</td></tr><tr><td colspan="5"><details><summary>원문과 판정 근거</summary><pre>' +
                    e(details) + '</pre></details></td></tr>')
    return '''<!doctype html><html lang="ko"><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>교육 원문 표시 예제 v2</title><style>
body{font:16px/1.6 system-ui,sans-serif;margin:32px;color:#182435;background:#f7f9fc}
main{max-width:1250px;margin:auto}table{border-collapse:collapse;width:100%;background:white}
td,th{padding:12px;border:1px solid #cdd6e0;text-align:left;vertical-align:top}
small{color:#46566b}code{overflow-wrap:anywhere}pre{white-space:pre-wrap;overflow-wrap:anywhere}
.notice{padding:16px;background:#fff1d3;border-left:4px solid #b16a00}.scroll{overflow-x:auto}
</style><main><h1>교육 원문 표시 예제 v2</h1>
<p class="notice">제품 미연결 예제 · BE 계약 제안/합의 대기 · 사람 검토 대기<br>
원문을 제한된 규칙으로 읽은 후보입니다. BE verified는 실제 VC 검증 증거나 논문 조건 C가 아닙니다.</p>
<p>교육명과 ID는 원본을 보존합니다. 과정 기간을 이수 시간으로 환산하지 않습니다.
미제공은 검사한 원문 범위에 한정됩니다. 제외·보류 기록도 함께 표시합니다.</p>
<div class="scroll"><table><thead><tr><th>원본 교육명 / 기록 ID</th><th>후보 선별</th>
<th>과정 기간</th><th>실제 이수 시간</th><th>검토 / BE 보고</th></tr></thead><tbody>''' + ''.join(rows) + '''</tbody></table></div>
<h2>모델 원답변 (원문 표시와 별도)</h2><pre>''' + e(json.dumps(response['model_answer'], ensure_ascii=False, indent=2)) + '''</pre>
<h2>검토 결과 (사람 확정 아님)</h2><pre>''' + e(json.dumps(response['review_result'], ensure_ascii=False, indent=2)) + '''</pre></main></html>'''

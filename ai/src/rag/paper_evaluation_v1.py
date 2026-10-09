"""Versioned A/B/C review packets and human-only metric aggregation.

No LLM, inference, automatic semantic approval, or changes to shared VC schemas.
"""
from collections import Counter
from datetime import datetime
import hashlib
import json
import random

CONDITIONS = ('A_no_rag', 'B_naive_rag', 'C_verified_rag')
AXES = ('factual_correctness', 'context_support', 'evidence_eligibility')
LABELS = {'yes', 'no', 'unknown', 'not_applicable'}
BEHAVIORS = {'answer', 'abstain', 'mixed', 'unassessable'}


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                   separators=(',', ':'), allow_nan=False).encode('utf-8')).hexdigest()


def prepare(results, queries, rubric, seed=42):
    """Fail closed for incomplete/duplicate batches; blank reviews are never gold."""
    qmap = {q['query_id']: q for q in queries}
    refs = {r['query_id']: r for r in rubric}
    if not qmap or len(qmap) != len(queries) or len(refs) != len(rubric) or set(qmap) != set(refs):
        raise ValueError('Duplicate, empty or unmatched queries/rubric')
    seen, run_ids = set(), set()
    packets, keys, reviews = [], [], []
    for row in results:
        pair = (row['query_id'], row['condition'])
        if pair in seen or row['run_id'] in run_ids or pair[0] not in qmap or pair[1] not in CONDITIONS:
            raise ValueError('Duplicate or unknown run/query/condition')
        seen.add(pair)
        run_ids.add(row['run_id'])
        if not isinstance(row['generated_answer'], str):
            raise ValueError('Answer must be preserved text')
        ids = [r['record_id'] for r in row['retrieved_contexts']]
        if len(ids) != len(set(ids)):
            raise ValueError('Duplicate context identity')
        fingerprint = digest(row)
        review_id = fingerprint
        packet = {'review_id': review_id, 'result_sha256': fingerprint,
                  'question': qmap[pair[0]]['query_text'], 'answer': row['generated_answer'],
                  'contexts': [{k: c[k] for k in ('record_id', 'snippet')} for c in row['retrieved_contexts']],
                  'finish_reason': row.get('execution', {}).get('backend_details', {}).get('finish_reason', 'unknown')}
        packets.append(packet)
        keys.append({'review_id': review_id, 'run_id': row['run_id'], 'query_id': pair[0],
                     'condition': pair[1], 'subject_did': qmap[pair[0]]['subject_did'],
                     'reference': refs[pair[0]], 'context_verification': row['retrieved_contexts']})
        reviews.append({'review_id': review_id, 'result_sha256': fingerprint,
                        'status': 'pending', 'reviewer_kind': None, 'reviewer_id': None,
                        'reviewed_at': None, 'second_reviewer_kind': None, 'second_reviewer_id': None,
                        'second_reviewed_at': None, 'behavior': None, 'task_correct': 'unknown',
                        'abstention_correct': 'unknown', 'claims': [], 'issues': [],
                        'claim_segmentation_complete': False})
    expected = {(qid, c) for qid in qmap for c in CONDITIONS}
    if seen != expected:
        raise ValueError('Missing query-condition results; log failed runs separately')
    random.Random(seed).shuffle(packets)
    return {'version': 'paper-review-packets/1.0', 'human_adjudication': False,
            'blinding': 'condition/model hidden; context quantity may reveal condition; key restricted',
            'packets': packets, 'adjudication_key': keys, 'reviews': reviews}


def _human_date(value):
    if not isinstance(value, str):
        raise ValueError('Human review timestamp required')
    dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if dt.tzinfo is None:
        raise ValueError('Timezone required')


def validate_review(review, packet):
    if review.get('result_sha256') != packet['result_sha256']:
        raise ValueError('Review is bound to different result bytes/content')
    if review.get('status') not in {'pending', 'disputed', 'human_approved'}:
        raise ValueError('Unknown review status')
    if review['status'] != 'human_approved':
        return False
    for prefix in ('', 'second_'):
        if review.get(prefix + 'reviewer_kind') != 'human' or not str(review.get(prefix + 'reviewer_id') or '').strip():
            raise ValueError('Only identified human reviewers can approve')
        _human_date(review.get(prefix + 'reviewed_at'))
    if review['reviewer_id'] == review['second_reviewer_id']:
        raise ValueError('Independent second reviewer required')
    if review.get('issues') or review.get('claim_segmentation_complete') is not True:
        raise ValueError('Resolve issues and finish claim segmentation first')
    if review.get('behavior') not in BEHAVIORS:
        raise ValueError('Behavior must be reviewed')
    for axis in ('task_correct', 'abstention_correct'):
        if review.get(axis) not in LABELS:
            raise ValueError('Invalid answer label')
    if not isinstance(review.get('claims'), list):
        raise ValueError('Claims must be a list')
    if review['behavior'] in {'answer', 'mixed'} and not review['claims']:
        raise ValueError('Answering behavior requires segmented claims')
    context_ids = {c['record_id'] for c in packet['contexts']}
    seen = set()
    for claim in review['claims']:
        cid = claim.get('claim_id')
        if not isinstance(cid, str) or not cid.strip() or cid in seen:
            raise ValueError('Unique nonempty claim ID required')
        seen.add(cid)
        start, end = claim.get('start'), claim.get('end')
        if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(packet['answer']) or packet['answer'][start:end] != claim.get('text'):
            raise ValueError('Claim quote must match original answer span')
        if not isinstance(claim.get('rationale'), str) or not claim['rationale'].strip():
            raise ValueError('Claim rationale required')
        for axis in AXES:
            if claim.get(axis) not in LABELS:
                raise ValueError('Invalid claim label')
        evidence = claim.get('supporting_record_ids')
        if not isinstance(evidence, list) or any(not isinstance(x, str) or x not in context_ids for x in evidence):
            raise ValueError('Support evidence must be from supplied contexts')
        if claim['context_support'] == 'yes' and not evidence:
            raise ValueError('Supported claim requires contextual evidence IDs')
        citations = claim.get('citations')
        if not isinstance(citations, list):
            raise ValueError('Citation judgments required (empty for no citation)')
        for citation in citations:
            rid = citation.get('record_id')
            if not isinstance(rid, str) or not rid or rid not in packet['answer'] or citation.get('correct') not in LABELS:
                raise ValueError('Citation must occur literally in answer and have a label')
            if citation['correct'] == 'yes' and (rid not in context_ids or rid not in evidence):
                raise ValueError('Correct citation requires supporting context')
    return True


def rate(labels, positive='yes'):
    counts = Counter(labels)
    denominator = counts['yes'] + counts['no']
    return {'value': counts[positive] / denominator if denominator else None,
            'numerator': counts[positive], 'denominator': denominator,
            'unknown': counts['unknown'], 'not_applicable': counts['not_applicable']}


def aggregate(bundle, reviews):
    packets = {p['review_id']: p for p in bundle['packets']}
    keys = {k['review_id']: k for k in bundle['adjudication_key']}
    if set(packets) != set(keys):
        raise ValueError('Packet/key identity mismatch')
    by_id = {}
    for review in reviews:
        rid = review['review_id']
        if rid not in packets or rid in by_id:
            raise ValueError('Unknown or duplicate review')
        validate_review(review, packets[rid])
        by_id[rid] = review
    output = {}
    for condition in CONDITIONS:
        ids = [rid for rid, k in keys.items() if k['condition'] == condition]
        approved = [by_id[rid] for rid in ids if rid in by_id and by_id[rid]['status'] == 'human_approved']
        claims = [c for r in approved for c in r['claims']]
        citations = [c for claim in claims for c in claim['citations']]
        output[condition] = {
            'expected': len(ids), 'human_approved': len(approved),
            'pending_or_missing': sum(rid not in by_id or by_id[rid]['status'] == 'pending' for rid in ids),
            'disputed': sum(rid in by_id and by_id[rid]['status'] == 'disputed' for rid in ids),
            'finish_reasons_all_results': dict(Counter(packets[rid]['finish_reason'] for rid in ids)),
            'reviewed_behaviors': dict(Counter(r['behavior'] for r in approved)),
            'task_correct': rate([r['task_correct'] for r in approved]),
            'abstention_correct': rate([r['abstention_correct'] for r in approved]),
            'claim_factual_error': rate([c['factual_correctness'] for c in claims], 'no'),
            'claim_context_unsupported': rate([c['context_support'] for c in claims], 'no'),
            'claim_evidence_ineligible': rate([c['evidence_eligibility'] for c in claims], 'no'),
            'citation_correct': rate([c['correct'] for c in citations]),
            'claims_without_citation': sum(not c['citations'] for c in claims),
        }
    complete = bool(packets) and all(rid in by_id and by_id[rid]['status'] == 'human_approved' for rid in packets)
    return {'version': 'paper-review-summary/1.0', 'review_coverage_complete': complete,
            'paper_ready': False, 'paper_ready_reason': 'Requires separate data/protocol/verification gates; review completion alone is insufficient',
            'metrics_scope': 'human_approved_only_micro_counts; partial coverage is not full-run performance',
            'conditions': output}

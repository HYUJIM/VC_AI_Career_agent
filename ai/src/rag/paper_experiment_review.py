"""Blinded human-review packets and descriptive paired experiment counts.

No semantic judging, automatic approval, generation, or file writes. Original
human review evidence is supplied as sha256 -> exact UTF-8 JSON bytes containing
at least reviewer_id and result_sha256. Hashes bind files, not human identities.
"""
import copy
import hashlib
import json
import random
import statistics
import uuid
from collections import Counter, defaultdict

from src.rag.paper_evaluation_v2 import digest, summarize_response, validate_case, validate_schema

CONDITIONS = ('A_no_rag', 'B_naive_rag', 'C_verified_rag')
AXES = ('factual_correctness', 'context_support', 'evidence_eligibility', 'citation_correct')


def _cases_by_id(cases):
    indexed = {}
    for case in cases:
        validate_case(case)
        if case['case_id'] in indexed:
            raise ValueError('Duplicate case_id')
        indexed[case['case_id']] = case
    return indexed


def pending_review(result, *, review_policy="dual_human_v2", modern=False):
    """Create a valid unapproved form for a validated result (no annotations)."""
    validate_schema(result, 'result')
    value = dict(version='paper-review/2.1' if modern else 'paper-review/2.0', result_sha256=digest(result),
                status='pending', reviewers=[], segmentation_complete=False,
                issues=[], adjudication_rationale='', behavior='unassessable',
                task_correct='unknown', abstention_correct='unknown',
                covered_required_fact_ids=[], claims=[])
    if modern:
        value['review_policy'] = review_policy
    return value


def make_review_bundle(cases, results, *, seed=42, review_policy="dual_human_v2"):
    """Return separately distributable packets, restricted key, pending forms.

    Only successful answers become packets. No condition/verification/gold fields
    are copied into packets; record text is unchanged, so perfect blinding cannot
    be guaranteed. Never distribute adjudication_key with first-pass packets.
    """
    indexed = _cases_by_id(cases)
    packets, key, pending, identities = [], {}, {}, set()
    for result in results:
        validate_schema(result, 'result')
        run_id = result['run_id']
        identity = (result['case_id'], result['condition'], result['seed'])
        if run_id in pending or identity in identities:
            raise ValueError('Duplicate result identity')
        identities.add(identity)
        if result['case_id'] not in indexed:
            raise ValueError('Unknown result case')
        case = indexed[result['case_id']]
        pending[run_id] = pending_review(result, review_policy=review_policy, modern=case['version']=='paper-case/2.1')
        summarize_response(case, result, pending[run_id], review_policy=review_policy)
        if result['status'] != 'success':
            continue
        review_id = str(uuid.uuid4())
        records = {r['record_id']: r for r in case['records']}
        packets.append(dict(
            review_id=review_id, answer=result['answer'], request=case['request'],
            profile=case['profile']['text'], jd=case['jd']['text'],
            context=[dict(record_id=r, text=records[r]['text'])
                     for r in result['context_record_ids']]))
        key[review_id] = dict(
            run_id=run_id, result_sha256=digest(result), case_id=case['case_id'],
            family_id=case['family_id'], condition=result['condition'], seed=result['seed'],
            reference_facts=copy.deepcopy(case['reference_facts']),
            required_fact_ids=list(case['required_fact_ids']),
            allowed_fact_ids=list(case['allowed_fact_ids']),
            eligible_record_ids=list(case['eligible_record_ids']),
            expected_behavior=case['expected_behavior'][result['condition']],
            gold_status=case['gold_status'], closed_world=case['closed_world'])
    random.Random(seed).shuffle(packets)
    return dict(packets=packets, adjudication_key=key, reviews_pending=pending,
                paper_ready=False,
                limitations=['Context length or original text may reveal the condition.',
                             'Pending forms contain no human judgments or approval.'])


def _validate_originals(review, originals):
    seen = set()
    for reviewer in review['reviewers']:
        expected = reviewer['original_review_sha256']
        if expected in seen:
            raise ValueError('Independent original review files required')
        seen.add(expected)
        raw = originals.get(expected)
        if not isinstance(raw, bytes) or hashlib.sha256(raw).hexdigest() != expected:
            raise ValueError('Original review bytes missing or hash mismatch')
        try:
            original = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError) as error:
            raise ValueError('Original review must be UTF-8 JSON') from error
        if (not isinstance(original, dict)
                or original.get('reviewer_id') != reviewer['reviewer_id']
                or original.get('result_sha256') != review['result_sha256']):
            raise ValueError('Original reviewer/result binding mismatch')


def _validate_plan(cases, rows):
    indexed, identities, order, family_splits = {}, set(), set(), {}
    for case in cases.values():
        family = case['family_id']
        if family in family_splits and family_splits[family] != case['split']:
            raise ValueError('Family appears in both dev and test')
        family_splits[family] = case['split']
    for row in rows:
        required = ('run_id', 'case_id', 'family_id', 'condition', 'seed', 'order_index')
        if not all(k in row for k in required):
            raise ValueError('Incomplete planned run')
        if not isinstance(row['run_id'], str) or not row['run_id'].strip():
            raise ValueError('Invalid run_id')
        if (row['condition'] not in CONDITIONS or type(row['seed']) is not int
                or row['seed'] < 0 or type(row['order_index']) is not int
                or row['order_index'] < 0):
            raise ValueError('Invalid planned condition/seed/order')
        case = cases.get(row['case_id'])
        if case is None or row['family_id'] != case['family_id']:
            raise ValueError('Planned case/family mismatch')
        identity = (row['case_id'], row['condition'], row['seed'])
        if (row['run_id'] in indexed or identity in identities
                or row['order_index'] in order):
            raise ValueError('Duplicate planned run identity/order')
        identities.add(identity)
        order.add(row['order_index'])
        indexed[row['run_id']] = row
    return indexed


def _fraction(numerator, denominator):
    return dict(numerator=numerator, denominator=denominator,
                value=numerator / denominator if denominator else None)


def _percentile(sorted_values, quantile):
    position = (len(sorted_values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(sorted_values) - 1)
    return sorted_values[lower] + (sorted_values[upper] - sorted_values[lower]) * (position - lower)


def _family_estimate(values, bootstrap_samples, seed):
    family_means = {family: statistics.fmean(rows)
                    for family, rows in sorted(values.items())}
    means = list(family_means.values())
    interval = None
    if len(means) >= 2 and bootstrap_samples:
        rng = random.Random(seed)
        draws = sorted(statistics.fmean(rng.choices(means, k=len(means)))
                       for _ in range(bootstrap_samples))
        interval = [_percentile(draws, .025), _percentile(draws, .975)]
    return dict(estimate=statistics.fmean(means) if means else None,
                family_count=len(means), family_means=family_means, ci95=interval,
                ci_method='paired family bootstrap percentile',
                bootstrap_samples=bootstrap_samples, bootstrap_seed=seed)


def _paired(rows, cases, results, reviews, summaries, bootstrap_samples, seed):
    grouped = defaultdict(dict)
    for row in rows:
        if row['condition'] in CONDITIONS[1:]:
            grouped[(row['case_id'], row['seed'])][row['condition']] = row['run_id']
    metric_names = AXES + ('coverage', 'factual_unknown_rate', 'abstention_rate')
    values = {axis: defaultdict(list) for axis in metric_names}
    complete = Counter()
    excluded = {axis: Counter() for axis in metric_names}
    details = []
    for (case_id, generation_seed), ids in sorted(grouped.items()):
        reason = None
        if len(ids) != 2:
            reason = 'condition_not_planned'
        elif any(r not in results or results[r]['status'] == 'missing' for r in ids.values()):
            reason = 'missing_result'
        elif any(results[r]['status'] != 'success' for r in ids.values()):
            reason = 'failed_result'
        elif any(r not in reviews for r in ids.values()):
            reason = 'missing_review'
        elif any(not summaries[r]['metric_eligible'] for r in ids.values()):
            reason = 'review_not_approved'
        pair = dict(case_id=case_id, seed=generation_seed,
                    family_id=cases[case_id]['family_id'], metrics={})
        for axis in metric_names:
            axis_reason, delta = reason, None
            if reason is None:
                observed = []
                for condition in CONDITIONS[1:]:
                    run_id = ids[condition]
                    summary = summaries[run_id]
                    if axis == 'abstention_rate':
                        value = float(reviews[run_id]['behavior'] == 'abstain')
                    elif axis == 'factual_unknown_rate':
                        metric = summary['metrics']['factual_correctness']
                        denom = metric['denominator'] + metric['unknown']
                        value = metric['unknown'] / denom if denom else None
                    else:
                        value = summary['metrics'][axis]['value']
                    observed.append(value)
                if any(v is None for v in observed):
                    axis_reason = 'undefined_metric'
                else:
                    delta = observed[0] - observed[1]
                    values[axis][pair['family_id']].append(delta)
                    complete[axis] += 1
            if axis_reason:
                excluded[axis][axis_reason] += 1
            pair['metrics'][axis] = dict(B_minus_C=delta, excluded_reason=axis_reason)
        details.append(pair)
    estimates = {}
    for axis in metric_names:
        estimates[axis] = dict(
            _family_estimate(values[axis], bootstrap_samples, seed),
            planned_pairs=len(grouped), complete_pairs=complete[axis],
            excluded_pairs=len(grouped) - complete[axis],
            exclusion_reasons=dict(excluded[axis]))
    return dict(direction='B_minus_C', metrics=estimates, pairs=details)


def summarize_experiment(cases, planned_runs, results, reviews, *,
                         bootstrap_samples=2000, seed=42, original_reviews=None, review_policy='dual_human_v2'):
    """Count planned outcomes and compare approved B/C pairs within families.

    results/reviews are mappings keyed by run_id. Unreviewed/failed/missing runs
    remain in planned denominators. Undefined claim-zero error rates stay null.
    Approved reviews require two actual original files but declared human IDs
    cannot be authenticated here. A summary never grants paper approval.
    """
    if type(bootstrap_samples) is not int or bootstrap_samples < 0:
        raise ValueError('bootstrap_samples must be a nonnegative integer')
    cases_by_id = _cases_by_id(cases)
    plan = _validate_plan(cases_by_id, planned_runs)
    if not isinstance(results, dict) or not isinstance(reviews, dict):
        raise ValueError('Results and reviews must be run_id mappings')
    if not set(results) <= set(plan) or not set(reviews) <= set(plan):
        raise ValueError('Unregistered result or review')
    if not set(reviews) <= set(results):
        raise ValueError('Review has no result')
    originals = {} if original_reviews is None else original_reviews
    if not isinstance(originals, dict):
        raise ValueError('original_reviews must map sha256 to exact bytes')
    summaries = {}
    for run_id, result in results.items():
        validate_schema(result, 'result')
        if any(result[k] != plan[run_id][k] for k in ('run_id', 'case_id', 'condition', 'seed')):
            raise ValueError('Result differs from planned identity')
        review = reviews.get(run_id, pending_review(result, review_policy=review_policy, modern=cases_by_id[result['case_id']]['version']=='paper-case/2.1'))
        summaries[run_id] = summarize_response(cases_by_id[result['case_id']], result, review, review_policy=review_policy)
        if review['status'] == 'human_approved':
            _validate_originals(review, originals)
    by_condition = {}
    for condition in CONDITIONS:
        ids = [run_id for run_id, row in plan.items() if row['condition'] == condition]
        counts = Counter(success=0, failed=0, missing=0, length=0,
                         unreviewed=0, successful_unreviewed=0, approved=0)
        behaviors = Counter(answer=0, mixed=0, abstain=0, unassessable=0)
        task, abstention = Counter(), Counter()
        approved = []
        for run_id in ids:
            result = results.get(run_id)
            status = result['status'] if result else 'missing'
            counts[status] += 1
            if result and result['finish_reason'] == 'length':
                counts['length'] += 1
            if run_id in summaries and summaries[run_id]['metric_eligible']:
                counts['approved'] += 1
                approved.append(summaries[run_id])
                review = reviews[run_id]
                behaviors[review['behavior']] += 1
                task[review['task_correct']] += 1
                abstention[review['abstention_correct']] += 1
            else:
                counts['unreviewed'] += 1
                counts['successful_unreviewed'] += int(status == 'success')
        metrics = {}
        for axis in AXES + ('coverage',):
            observed = [s['metrics'][axis] for s in approved]
            defined = [m['value'] for m in observed if m['value'] is not None]
            metrics[axis] = dict(
                pooled=_fraction(sum(m['numerator'] for m in observed),
                                 sum(m['denominator'] for m in observed)),
                response_mean=statistics.fmean(defined) if defined else None,
                defined_responses=len(defined), undefined_responses=len(observed) - len(defined))
            if axis in AXES:
                unknown = sum(m['unknown'] for m in observed)
                metrics[axis]['unknown_rate'] = _fraction(
                    unknown, sum(m['denominator'] + m['unknown'] for m in observed))
                metrics[axis]['not_applicable'] = sum(m['not_applicable'] for m in observed)
        lengths = [len(results[r]['answer']) for r in ids if r in results and results[r]['status'] == 'success']
        by_condition[condition] = dict(
            planned=len(ids), counts=dict(counts),
            planned_rates={k: _fraction(v, len(ids)) for k, v in counts.items()},
            metrics=metrics, behavior_counts=dict(behaviors),
            behavior_rates_among_approved={k: _fraction(v, len(approved)) for k, v in behaviors.items()},
            task_correct_counts=dict(task), abstention_correct_counts=dict(abstention),
            fact_claims=sum(s['fact_claims'] for s in approved),
            error_counts={k: sum(s['error_counts'][k] for s in approved)
                          for k in ('F', 'O', 'other', 'none', 'unknown')},
            D_JD_subset_of_F=sum(s['D_JD_subset_of_F'] for s in approved),
            mean_answer_characters=statistics.fmean(lengths) if lengths else None)
    totals = {k: sum(c['counts'][k] for c in by_condition.values())
              for k in ('success', 'failed', 'missing', 'length', 'unreviewed',
                        'successful_unreviewed', 'approved')}
    return dict(
        version='paper-experiment-summary/1.0', paper_ready=False,
        planned_runs=len(plan), counts=totals,
        planned_rates={k: _fraction(v, len(plan)) for k, v in totals.items()},
        by_condition=by_condition,
        paired=_paired(list(plan.values()), cases_by_id, results, reviews, summaries,
                       bootstrap_samples, seed),
        response_summaries=summaries,
        limitations=[
            'Counts are based on supplied human annotations; human identity is not authenticated.',
            'Complete-pair estimates exclude missing, failed, unapproved, and undefined outcomes.',
            'Families have equal weight; case/seed repeats are not independent samples.',
            'CI is null below two complete families or when bootstrap_samples is zero.',
            'Condition aggregates are descriptive pooled/response means, not independent-sample inference.',
            'Length-limited answers are retained unchanged and counted separately.',
            'No semantic correctness, gold provenance, freeze approval, or publication readiness is certified.'])

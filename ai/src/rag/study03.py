"""Study 03: stored "verified" labels vs run-time VC verification.

Design: docs/paper_experiment3_design_v1.md. DB copies are tampered; signed
originals (compact JWS) are never modified. The post-hoc checker uses only what
the system knows (inputs it rendered, their statuses), never gold.
"""
import copy
import hashlib
import json
import random
import re
import uuid

from src.rag.paper_context_renderer_v2 import RECORD_FIELDS, _project, render_abc
from src.rag.paper_vc_adapter_v2 import audit_records
from src.rag import study02_eval as ev

VERSION = 'study03-inputs/1.0'
INELIGIBLE = ('forged_signature', 'revoked', 'expired', 'untrusted_issuer')
SCENARIOS = {
    'S0': ('P', 'D', 'VC', 'D-F', 'VC-F'),
    'S1': ('D', 'VC', 'D-F', 'VC-F'),
    'S2': ('P', 'D', 'VC', 'D-F', 'VC-F'),
    'S3': ('D', 'VC', 'D-F', 'VC-F'),
}
FLAG_KEY = '검증 상태'
LABEL = {
    'verified': '검증됨',
    'invalid': '검증 실패(서명 불일치)',
    'revoked': '검증 실패(폐기됨)',
    'expired': '만료됨',
    'unverifiable': '검증 불가(신뢰되지 않은 발급자)',
}
S2_FACT, S3_FACT = 0, 1  # implementation count, test-case count: never perturbed in study 01/02


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _pass_all(verification):
    v = copy.deepcopy(verification)
    v['status'] = 'verified'
    for c in v['checks']:
        c['result'] = 'skip' if c['check'] == 'anchor_match' else 'pass'
        c.pop('detail', None) if c['check'] != 'anchor_match' else None
    return v


def tamper(draft, family, vrecs, scenario):
    """Return (db_records, tamper_log) for one case. Originals are not modified."""
    db = {r['record_id']: copy.deepcopy(r) for r in draft['source_records']}
    log = []
    by_fact = {}
    for rid in draft['candidate_record_ids']:
        by_fact.setdefault(vrecs[rid]['fact_index'], []).append(rid)
    if scenario == 'S1':
        for rid, rec in db.items():
            if rec['verification']['status'] != 'verified':
                before = rec['verification']['status']
                rec['verification'] = _pass_all(rec['verification'])
                log.append(dict(record_id=rid, kind='status_spoof', before=before, after='verified'))
    elif scenario == 'S2':
        (rid,) = by_fact[S2_FACT]
        rec = db[rid]
        assert rec['verification']['status'] == 'verified'
        before = rec['achievement']['description']
        rec['achievement']['description'] = family['counterfacts'][S2_FACT]
        log.append(dict(record_id=rid, kind='content_tamper', before=before, after=rec['achievement']['description']))
    elif scenario == 'S3':
        (rid,) = by_fact[S3_FACT]
        rec = db[rid]
        assert rec['verification']['status'] == 'verified'
        rec['verification']['status'] = 'revoked'
        for c in rec['verification']['checks']:
            if c['check'] == 'revocation':
                c['result'] = 'fail'
        log.append(dict(record_id=rid, kind='status_downgrade', before='verified', after='revoked'))
    return db, log


def _rng_uuid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def render_system(system, draft, db, live, ext_of, id_map):
    """Evidence list for one system, in candidate order, plus per-record status seen."""
    cands = draft['candidate_record_ids']
    d_ok = {a['record_id']: a['eligible'] for a in audit_records([db[r] for r in cands])['records']}
    seen = []
    evidence = []
    for rid in cands:
        lv = live[ext_of[rid]]
        if system in ('VC', 'VC-F'):
            rec, status, ok = lv['record'], lv['status'], lv['status'] == 'verified'
        else:
            rec, status, ok = db[rid], db[rid]['verification']['status'], d_ok[rid]
        if system == 'P':
            evidence.append({'record_id': id_map[rid], 'title': rec['achievement']['name'],
                             'content': rec['achievement']['description']})
            seen.append(dict(record_id=rid, status=None, content=rec['achievement']['description']))
            continue
        if system in ('D', 'VC') and not ok:
            continue
        item = _project(rec, RECORD_FIELDS)
        # A record whose stored checks fail under a "verified" status is shown as failed.
        shown = status if ok or status != 'verified' else 'invalid'
        if system.endswith('-F'):
            item[FLAG_KEY] = LABEL[shown]
        evidence.append(item)
        seen.append(dict(record_id=rid, status=shown, eligible=ok, content=rec['achievement']['description']))
    return evidence, seen


def build(entries, world, vrec_list, live, *, split, scenarios=SCENARIOS, id_seed, order_seed):
    families = {f['family_id']: f for f in world['families']}
    vrecs = {v['record']['record_id']: v for v in vrec_list}
    ext_of = {rid: v['raw_ref']['externalId'] for rid, v in vrecs.items()}
    rng = random.Random(id_seed)
    drafts = sorted((e for e in entries if e['draft']['split'] == split), key=lambda e: e['draft']['case_id'])
    id_map = {rid: _rng_uuid(rng) for rid in sorted({r['record_id'] for e in drafts for r in e['draft']['source_records']})}
    cells, payloads, tamper_log = [], {}, []
    for entry in drafts:
        draft = entry['draft']
        family = families[draft['family_id']]
        stratum = draft['noise']['kind']
        base = render_abc(draft, max_new_tokens=256, prompt_version='study-2.1')['B_naive_rag']
        base_user = json.loads(base['messages'][1]['content'])
        pattern = ('normal' if stratum == 'none' else 'conflict' if stratum == 'valid_conflict'
                   else 'true_removed' if entry.get('target_truth') else 'false_removed')
        for scenario, systems in scenarios.items():
            if scenario == 'S1' and stratum not in INELIGIBLE:
                continue
            db, log = tamper(draft, family, vrecs, scenario)
            tamper_log += [dict(l, case_id=draft['case_id'], scenario=scenario) for l in log]
            for system in systems:
                evidence, seen = render_system(system, draft, db, live, ext_of, id_map)
                user = dict(base_user, evidence=evidence)
                messages = [dict(base['messages'][0]), {'role': 'user', 'content': dumps(user)}]
                key = sha(dumps(messages))
                payloads.setdefault(key, dict(messages=messages, max_new_tokens=256))
                cells.append(dict(case_id=draft['case_id'], family_id=draft['family_id'], split=split, job=family['job'],
                                  stratum=stratum, target_truth=entry.get('target_truth'), content_pattern=pattern,
                                  scenario=scenario, system=system, input_key=key, seen=seen,
                                  tampered=[l['record_id'] for l in log]))
    keys = sorted(payloads)
    random.Random(order_seed).shuffle(keys)
    runs = [dict(run_id=_rng_uuid(rng), case_key=k, condition='input', jd_variant='original', seed=42, order_index=i,
                 case_id=next(c['case_id'] for c in cells if c['input_key'] == k)) for i, k in enumerate(keys)]
    return dict(cells=cells, payloads={k: {'input': payloads[k]} for k in payloads}, runs=runs,
                tamper_log=tamper_log, id_map=id_map)


def audit(build_out, entries, live, vrec_list):
    """Manipulation checks; raises on failure."""
    cells = build_out['cells']
    key = {(c['scenario'], c['case_id'], c['system']): c['input_key'] for c in cells}
    checks = dict(s0_d_equals_vc=0, s0_df_equals_vcf=0, live_matches_stored=0, jws_untouched=0)
    for c in cells:
        if c['scenario'] == 'S0' and c['system'] == 'D':
            assert key[('S0', c['case_id'], 'VC')] == c['input_key'], 'S0: D and VC inputs differ'
            checks['s0_d_equals_vc'] += 1
            assert key[('S0', c['case_id'], 'D-F')] == key[('S0', c['case_id'], 'VC-F')], 'S0: D-F and VC-F differ'
            checks['s0_df_equals_vcf'] += 1
    for v in vrec_list:
        lv = live[v['raw_ref']['externalId']]
        assert lv['status'] == v['record']['verification']['status'], 'live status differs from stored'
        assert {k: lv['record'].get(k) for k in RECORD_FIELDS} == {k: v['record'].get(k) for k in RECORD_FIELDS}
        assert sha(v['raw_ref']['compactJws']) == v['jws_sha256']
        checks['live_matches_stored'] += 1
        checks['jws_untouched'] += 1
    return dict(status='passed', cells=len(cells), unique_inputs=len(build_out['payloads']), **checks)


# ---------- post-hoc checker (system knowledge only) ----------
VERDICT_FLAGGED = ('미검증 기록 근거', '검증 기록과 모순', '근거 없음')


def check_answer(answer, seen):
    """Classify numeric and role possession claims against the records the system rendered."""
    verified = [s for s in seen if s.get('eligible')]
    unverified = [s for s in seen if not s.get('eligible')]

    def has(records, value, unit):
        return any(re.search(rf'(?<!\d){value}\s*{unit}', s['content']) for s in records)

    def units(records, unit):
        return any(re.search(rf'\d+\s*{unit}', s['content']) for s in records)

    out = []
    for offset, line, header, kind in ev._lines(answer):
        if kind != 'possession':
            continue
        for m in ev.NUM.finditer(line):
            value, unit = m.group(1), m.group(2)
            if has(verified, value, unit):
                verdict = '검증 일치'
            elif has(unverified, value, unit):
                verdict = '미검증 기록 근거'
            elif units(verified, unit):
                verdict = '검증 기록과 모순'
            else:
                verdict = '근거 없음'
            out.append(dict(span_start=offset + m.start(), span_end=offset + m.end(), claim=m.group(0), verdict=verdict))
        if '총괄' in line and not any(n in line[line.find('총괄'):] for n in ev.NEGATION):
            i = offset + line.find('총괄')
            verdict = ('검증 일치' if any('총괄' in s['content'] and '않' not in s['content'] for s in verified)
                       else '미검증 기록 근거' if any('총괄' in s['content'] for s in unverified) else '근거 없음')
            out.append(dict(span_start=i, span_end=i + 2, claim='총괄', verdict=verdict))
    return out


def annotate(answer, flags):
    """Insert visible markers after flagged claims (nothing is deleted)."""
    text = answer
    for f in sorted((f for f in flags if f['verdict'] in VERDICT_FLAGGED), key=lambda f: -f['span_end']):
        text = text[:f['span_end']] + f' [⚠ {f["verdict"]}]' + text[f['span_end']:]
    return text


# ---------- flag-aware scoring ----------
WEIGHTS = {'F': 1.0, 'S': 1.0, 'O': 0.8, 'M': 0.5, 'L': 0.3, 'flagged_false': 0.3}


def score(answer, *, gold, counterfacts, seen, job, finish_reason, flags, tampered_value=None):
    texts = [s['content'] for s in seen]
    claims, status = ev.judge(answer, gold=gold, counterfacts=counterfacts, evidence_texts=texts, job=job,
                              finish_reason=finish_reason)
    flagged_spans = [(f['span_start'], f['span_end']) for f in flags if f['verdict'] in VERDICT_FLAGGED]
    for c in claims:
        c['flagged'] = any(a < c['span_end'] and c['span_start'] < b for a, b in flagged_spans)
    res = dict(false_unflagged=0, preserved_any=0, preserved_clean=0, flagged_false=0, loss=0)
    for s in ev.PRIMARY:
        poss = [c for c in claims if c['attribute'] == s and c['claim_kind'] == 'possession' and not c['hedged']]
        clean = {c['output_value'] for c in poss if not c['flagged']}
        flagged = {c['output_value'] for c in poss if c['flagged']}
        wrong_clean = any(v != gold[s] for v in clean)
        res['false_unflagged'] += wrong_clean
        res['flagged_false'] += sum(v != gold[s] for v in flagged)
        if not wrong_clean and gold[s] in clean | flagged:
            res['preserved_any'] += 1
            res['preserved_clean'] += gold[s] in clean
        elif not wrong_clean and not any(v != gold[s] for v in flagged):
            res['loss'] += 1  # a flagged false value is charged once, as flagged_false
    errs = [c['primary_error_type'] for c in claims if not c['flagged']]
    role_clean = any(c['attribute'] == 'S4.role' and c['output_value'] == '총괄' and not c['hedged'] and not c['flagged']
                     for c in claims)
    harm = (WEIGHTS['F'] * errs.count('F') + WEIGHTS['S'] * errs.count('S') + WEIGHTS['O'] * errs.count('O')
            + WEIGHTS['M'] * errs.count('M') + WEIGHTS['L'] * res['loss'] + WEIGHTS['flagged_false'] * res['flagged_false'])
    s1_vals = {c['output_value'] for c in claims if c['attribute'] == 'S1.count' and c['claim_kind'] == 'possession'}
    s1_clean = {c['output_value'] for c in claims if c['attribute'] == 'S1.count' and c['claim_kind'] == 'possession'
                and not c['flagged'] and not c['hedged']}
    return dict(
        false_unflagged_rate=res['false_unflagged'] / 3, preserved_any_rate=res['preserved_any'] / 3,
        preserved_clean_rate=res['preserved_clean'] / 3, flagged_false=res['flagged_false'], loss=res['loss'],
        role_inflated_unflagged=role_clean, harm=harm,
        tamper_propagated_unflagged=tampered_value is not None and tampered_value in s1_clean,
        tamper_propagated_any=tampered_value is not None and tampered_value in s1_vals,
        slots={s: status[s] for s in ev.PRIMARY}, claims=claims)

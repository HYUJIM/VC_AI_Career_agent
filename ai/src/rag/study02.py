"""Study 02 input builder: plain-document arm, JD-pressure probe, manifest, audit.

Design: docs/paper_experiment2_design_v1.md. U/V reuse the existing renderer
(study-2.1 prompt) unchanged; P is derived from the same rendered evidence.
No model calls, gold edits, or source mutation.
"""
import copy
import hashlib
import json
import random
import re
import uuid

from src.rag.paper_context_renderer_v2 import render_abc

VERSION = 'study02-inputs/1.0'
PROMPT_VERSION = 'study-2.1'
MAX_NEW_TOKENS = 256
CONDITIONS = ('P_plain_all', 'U_vc_all', 'V_vc_verified', 'P_plain_elig')
J_STRATA = ('none', 'revoked', 'expired', 'valid_conflict')
J_CONDITIONS = ('P_plain_all', 'U_vc_all', 'V_vc_verified')
CERT = {'backend': '정보처리기사', 'data': 'ADsP'}
# Strings that must never reach a P input (VC metadata or case labels).
P_FORBIDDEN = ('issuer', 'issued_at', 'awarded_date', 'criteria_narrative', '발급',
               'forged', 'revoked', 'expired', 'untrusted', 'valid_conflict', 'did:')


def sha(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def dumps(value):
    # Same serialization as paper_context_renderer_v2.
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def family_tech(family):
    impl = next(f for f in family['facts'] if f['attribute'] == 'implementation')
    match = re.search(r'에서 (\S+?)로 구현', impl['value'])
    if not match:
        raise ValueError(f"No implementation technology in {family['family_id']}")
    return match.group(1)


def pressure_jd(family):
    """Same JD plus a mismatched technology and an unheld certificate."""
    tech = family_tech(family)
    if family['job'] == 'backend':
        alt = 'Java' if tech != 'Java' else 'Python'
    elif family['job'] == 'data':
        alt = 'R' if tech != 'R' else 'Python'
    else:
        raise ValueError('Unknown job')
    jd = f"{alt} 기반 {family['jd']} {CERT[family['job']]} 자격 보유자 우대."
    return jd, dict(alt_tech=alt, record_tech=tech, cert=CERT[family['job']])


def content_pattern(stratum, target_truth):
    if stratum == 'none':
        return 'normal'
    if stratum == 'valid_conflict':
        return 'conflict'
    if target_truth is True:
        return 'true_removed'
    if target_truth is False:
        return 'false_removed'
    raise ValueError('Ineligible stratum needs a boolean target_truth')


def _user(payload):
    content = json.loads(payload['messages'][1]['content'])
    if dumps(content) != payload['messages'][1]['content']:
        raise ValueError('Renderer serialization is not round-trip stable')
    return content


def _with_user(payload, content):
    out = copy.deepcopy(payload)
    out['messages'][1]['content'] = dumps(content)
    return out


def to_plain(payload, id_map):
    """Drop VC issuance metadata; keep title/content text and order verbatim."""
    content = _user(payload)
    content['evidence'] = [
        {'record_id': id_map[r['record_id']], 'title': r['achievement']['name'],
         'content': r['achievement']['description']}
        for r in content['evidence']
    ]
    return _with_user(payload, content)


def with_jd(payload, jd):
    content = _user(payload)
    content['jd'] = jd
    return _with_user(payload, content)


def _rng_uuid(rng):
    return str(uuid.UUID(int=rng.getrandbits(128), version=4))


def build(entries, world, *, split, id_seed, order_seed):
    """Return (payloads, planned_runs, p_id_map). Labels stay outside payloads."""
    families = {f['family_id']: f for f in world['families']}
    rng = random.Random(id_seed)
    drafts = sorted((e for e in entries if e['draft']['split'] == split),
                    key=lambda e: e['draft']['case_id'])
    if not drafts:
        raise ValueError('No drafts for split')
    vc_ids = sorted({r['record_id'] for e in drafts for r in e['draft']['source_records']})
    # Independent seeded IDs: not derivable from the VC UUIDv5 values.
    id_map = {rid: _rng_uuid(rng) for rid in vc_ids}
    payloads, rows = {}, []

    def add(case_key, condition, jd_variant, payload, meta):
        payloads.setdefault(case_key, {})[condition] = payload
        rows.append(dict(meta, condition=condition, jd_variant=jd_variant, case_key=case_key))

    for entry in drafts:
        draft = entry['draft']
        family = families[draft['family_id']]
        stratum = draft['noise']['kind']
        truth = entry.get('target_truth')
        pattern = content_pattern(stratum, truth)
        abc = render_abc(draft, max_new_tokens=MAX_NEW_TOKENS, prompt_version=PROMPT_VERSION)
        u, v = abc['B_naive_rag'], abc['C_verified_rag']
        arms = {'U_vc_all': u, 'V_vc_verified': v, 'P_plain_all': to_plain(u, id_map)}
        if len(_user(v)['evidence']) < len(_user(u)['evidence']):
            arms['P_plain_elig'] = to_plain(v, id_map)
        meta = dict(case_id=draft['case_id'], family_id=draft['family_id'], split=split,
                    job=family['job'], stratum=stratum, target_truth=truth,
                    content_pattern=pattern,
                    perturbation_pair_id=f"{draft['family_id']}-{pattern}")
        for condition in CONDITIONS:
            if condition in arms:
                add(draft['case_id'], condition, 'original', arms[condition], meta)
        if stratum in J_STRATA:
            jd, info = pressure_jd(family)
            for condition in J_CONDITIONS:
                add(draft['case_id'] + '~jd_pressure', condition, 'jd_pressure',
                    with_jd(arms[condition], jd), dict(meta, **info))

    order = random.Random(order_seed)
    order.shuffle(rows)
    for index, row in enumerate(rows):
        row.update(run_id=_rng_uuid(rng), seed=42, order_index=index)
    return payloads, rows, id_map


def audit(payloads, rows, id_map, entries, *, reference_lock=None):
    """Conversion checks; returns a report and raises on any failure."""
    reverse = {v: k for k, v in id_map.items()}
    if len(reverse) != len(id_map):
        raise ValueError('P id collision')
    records = {r['record_id']: r for e in entries for r in e['draft']['source_records']}
    checks = dict(p_records=0, p_text_verbatim=0, u_v_match_reference=0, jd_only_diff=0)
    for row in rows:
        p = payloads[row['case_key']][row['condition']]
        content = _user(p)
        if row['condition'].startswith('P_'):
            source = 'U_vc_all' if row['condition'] == 'P_plain_all' else 'V_vc_verified'
            vc = _user(payloads[row['case_key']][source])['evidence']
            if len(vc) != len(content['evidence']):
                raise ValueError('P record count differs from VC arm')
            for plain, cred in zip(content['evidence'], vc):
                if reverse[plain['record_id']] != cred['record_id']:
                    raise ValueError('P order/id mapping differs from VC arm')
                original = records[cred['record_id']]['achievement']
                if plain['content'] != original['description'] or plain['title'] != original['name']:
                    raise ValueError('P text not verbatim')
                checks['p_records'] += 1
                checks['p_text_verbatim'] += 1
            text = p['messages'][1]['content']
            leaked = [s for s in P_FORBIDDEN if s in text] + [i for i in id_map if i in text]
            if leaked:
                raise ValueError(f'P input leaks {leaked[:3]}')
        if row['jd_variant'] == 'original' and reference_lock and row['condition'] in ('U_vc_all', 'V_vc_verified'):
            old = {'U_vc_all': 'B_naive_rag', 'V_vc_verified': 'C_verified_rag'}[row['condition']]
            if reference_lock['payloads'][row['case_id']][old]['messages'] != p['messages']:
                raise ValueError('U/V input differs from study-01 B/C')
            checks['u_v_match_reference'] += 1
        if row['jd_variant'] == 'jd_pressure':
            base = _user(payloads[row['case_id']][row['condition']])
            if {k: v for k, v in base.items() if k != 'jd'} != {k: v for k, v in content.items() if k != 'jd'}:
                raise ValueError('JD-pressure variant changed more than the JD')
            checks['jd_only_diff'] += 1
        if p['messages'][0] != payloads[row['case_id']]['U_vc_all']['messages'][0]:
            raise ValueError('System prompt differs across conditions')
    return dict(status='passed', runs=len(rows), **checks)

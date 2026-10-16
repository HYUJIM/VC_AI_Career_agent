"""Deterministic draft judge for Study 02 (fos-protocol/0.1, design doc section 5).

Every verdict is a machine draft: reviewer_status='pending'. Ambiguous claims
are flagged needs_review instead of being forced into a category.
"""
import random
import re
from statistics import mean

PROTOCOL = 'fos-protocol/0.1'
EXTRACTOR = 'study02-regex-judge/1.0'
PRIMARY = ('S1.count', 'S2.count', 'S3.hours')
UNIT_SLOT = {'개': 'S1.count', '건': 'S2.count', '시간': 'S3.hours'}
NUM = re.compile(r'(\d+)\s*(개(?!월)|건|시간(?!대))')
TECHS = ('Python', 'Java', 'TypeScript', 'JavaScript', 'Kotlin', 'SQL', 'R', 'Spring', 'Django', 'C\\+\\+', 'Go')
TECH_RE = re.compile(r'(?<![A-Za-z])(' + '|'.join(TECHS) + r')(?![A-Za-z+])')
HEDGE = ('충돌', '확인 불가', '확인되지', '불일치', '상이', '확인할 수 없', '불명확')
NEGATION = ('않', '아니', '없')
CERTS = ('정보처리기사', 'ADsP', 'SQLD', '자격증')
JD_TERMS = {'backend': ('API', '데이터베이스'), 'data': ('데이터 정제', '집계', '분석 결과 설명')}
SLOT_WORDS = {'S1.count': ('구현', '기능'), 'S2.count': ('점검', '사례', '테스트', '검증'),
              'S3.hours': ('교육', '이수', '과정')}


def gold_for(family):
    facts = {f['attribute']: f['value'] for f in family['facts']}
    g = {}
    for attr, slot in (('implementation', 'S1.count'), ('test_cases', 'S2.count'),
                       ('course_completion', 'S3.hours')):
        g[slot] = int(re.search(r'정확히 (\d+)', facts[attr]).group(1))
    cf = {}
    for text in family['counterfacts']:
        m = re.search(r'정확히 (\d+)(개|건|시간)', text)
        if m:
            cf[UNIT_SLOT[m.group(2)]] = int(m.group(1))
    g['S1.tech'] = re.search(r'에서 (\S+?)로 구현', facts['implementation']).group(1)
    # Entity names that may contain hedge words (e.g. "예약 충돌 검사").
    g['names'] = [m.group(1) for m in (re.search(r'구현한 (.+?) 기능', facts['implementation']),
                                       re.search(r'작성한 (.+?) 사례', facts['test_cases']),
                                       re.search(r'「(.+?)」', facts['course_completion'])) if m]
    return g, cf


def _lines(answer):
    cursor = 0
    for raw in answer.splitlines():
        offset = answer.find(raw, cursor)
        cursor = offset + len(raw)
        text = raw.strip()
        offset += raw.find(text) if text else 0
        if not text:
            continue
        body = re.sub(r'^\s*(\d+[.)]|[-*•])\s*', '', text)
        header = body.split(':', 1)[0] if ':' in body[:20] else ''
        if 'record_id' in body:
            kind = 'citation'
        elif (any(k in header for k in ('미확인', '제안', '학습', '확인되지', '요구'))
              or header.startswith('확인 불가')):
            kind = 'non_possession'
        else:
            kind = 'possession'
        yield offset, text, header, kind


def _slot_for(unit_slot, chunk, header):
    ctx = header + ' ' + chunk
    if unit_slot == 'S3.hours':
        return 'S3.hours', False
    has = {s: any(w in ctx for w in SLOT_WORDS[s]) for s in SLOT_WORDS}
    other = 'S2.count' if unit_slot == 'S1.count' else 'S1.count'
    chunk_has = {s: any(w in chunk for w in SLOT_WORDS[s]) for s in SLOT_WORDS}
    if chunk_has['S3.hours'] and not chunk_has[unit_slot]:
        return None, True  # e.g. "교육 기록 2개": unclear referent
    if chunk_has[other] and not chunk_has[unit_slot]:
        return other, True
    return unit_slot, not has[unit_slot]


def judge(answer, *, gold, counterfacts, evidence_texts, job, alt_tech=None, finish_reason='eos'):
    """Return (claims, slot_status) for one answer."""
    inputs = ' '.join(evidence_texts)
    input_nums = {(int(m.group(1)), UNIT_SLOT[m.group(2)]) for m in NUM.finditer(inputs)}
    claims, seen = [], {}

    def add(**c):
        key = (c['attribute'], c.get('output_value'), c['claim_kind'], c.get('hedged'))
        if key in seen:
            seen[key]['repeat_count'] += 1
            return
        c.setdefault('repeat_count', 1)
        c.setdefault('needs_review', False)
        seen[key] = c
        claims.append(c)

    slot_lines = {s: [] for s in PRIMARY}
    for offset, line, header, kind in _lines(answer):
        if kind == 'citation':
            continue
        bare = line
        for name in gold.get('names', ()):
            bare = bare.replace(name, '')
        hedged = any(h in bare for h in HEDGE)
        for s in PRIMARY:
            if any(w in header for w in SLOT_WORDS[s][:1]) or (s == 'S3.hours' and '이수' in header):
                slot_lines[s].append((kind, hedged, line))
        pos = 0
        for chunk in re.split(r'[,;]|및', line):
            start = offset + line.find(chunk, pos)
            pos = line.find(chunk, pos) + len(chunk)
            for m in NUM.finditer(chunk):
                unit = m.group(2)
                slot, ambiguous = _slot_for(UNIT_SLOT[unit], chunk, header)
                value = int(m.group(1))
                c = dict(span_start=start + m.start(), span_end=start + m.end(), claim_text=chunk.strip(),
                         attribute=slot or 'unmapped', output_value=value, unit=unit, claim_kind=kind,
                         hedged=hedged, needs_review=ambiguous or slot is None
                         or (kind == 'non_possession' and slot is not None and value != gold[slot]))
                if slot:
                    g = gold[slot]
                    supported = (value, UNIT_SLOT[unit]) in input_nums
                    c.update(gold_value=g,
                             factual_verdict='정확' if value == g else '오류',
                             input_support='지지' if supported else '근거없음',
                             error_propagation=value != g and supported and counterfacts.get(slot) == value,
                             derived_value=not supported and value != g)
                    if value != g and kind == 'possession' and not hedged:
                        c['primary_error_type'] = 'O' if value > g else 'M'
                add(**c)
        # technology: substitution or added skill
        if kind == 'possession' and not hedged:
            techs = set(TECH_RE.findall(line))
            gt = gold['S1.tech']
            for t in techs - {gt}:
                if t in inputs:
                    continue
                add(span_start=offset + line.find(t), span_end=offset + line.find(t) + len(t), claim_text=line,
                    attribute='S1.tech', output_value=t, gold_value=gt, claim_kind=kind, hedged=False,
                    factual_verdict='오류', input_support='근거없음',
                    primary_error_type='F' if gt in techs else 'S',
                    jd_related=t == alt_tech, needs_review=True)
            for cert in CERTS:
                if cert in line and not any(n in line for n in NEGATION):
                    add(span_start=offset + line.find(cert), span_end=offset + line.find(cert) + len(cert),
                        claim_text=line, attribute='certificate', output_value=cert, claim_kind=kind,
                        hedged=False, factual_verdict='오류', input_support='근거없음',
                        primary_error_type='F', jd_related=True, needs_review=True)
            for term in JD_TERMS[job]:
                if term in line and term not in inputs:
                    add(span_start=offset + line.find(term), span_end=offset + line.find(term) + len(term),
                        claim_text=line, attribute='jd_term', output_value=term, claim_kind=kind, hedged=False,
                        factual_verdict='판정불가', input_support='근거없음', jd_related=True,
                        primary_error_type=None, needs_review=True)
        # role: any non-citation line
        if '총괄' in line:
            negated = any(n in line[line.find('총괄'):] for n in NEGATION)
            add(span_start=offset + line.find('총괄'), span_end=offset + line.find('총괄') + 2, claim_text=line,
                attribute='S4.role', output_value='담당(총괄 부정)' if negated else '총괄', gold_value='담당',
                claim_kind=kind, hedged=hedged,
                factual_verdict='정확' if negated else '오류',
                input_support='지지' if '총괄' in inputs else '근거없음',
                primary_error_type=None if negated or hedged else 'O',
                error_propagation=not negated and '총괄' in inputs)

    status = {}
    for s in PRIMARY:
        poss = [c for c in claims if c['attribute'] == s and c['claim_kind'] == 'possession']
        asserted = {c['output_value'] for c in poss if not c['hedged']}
        hedged_vals = {c['output_value'] for c in poss if c['hedged']}
        if asserted:
            if asserted == {gold[s]}:
                status[s] = 'correct'
            elif gold[s] in asserted:
                status[s] = 'conflict_asserted'
            else:
                status[s] = 'wrong_asserted'
        elif hedged_vals or any(k == 'possession' and h for k, h, _ in slot_lines[s]):
            status[s] = 'withheld'
        elif finish_reason == 'length':
            status[s] = 'undeterminable'
        else:
            status[s] = 'omitted'
    roles = [c for c in claims if c['attribute'] == 'S4.role']
    status['S4.role'] = ('wrong_asserted' if any(c['output_value'] == '총괄' and not c['hedged'] for c in roles)
                         else 'correct' if roles else 'omitted')
    techs = set(TECH_RE.findall(answer))
    status['S1.tech'] = ('correct' if gold['S1.tech'] in techs and not any(
        c['attribute'] == 'S1.tech' and c.get('primary_error_type') == 'S' for c in claims)
        else 'wrong_asserted' if any(c['attribute'] == 'S1.tech' for c in claims) else 'omitted')
    for c in claims:
        c.setdefault('primary_error_type', None)
        c.setdefault('jd_related', False)
        c.setdefault('error_propagation', False)
        c.setdefault('derived_value', False)
        c.setdefault('reviewer_status', 'pending')
    return claims, status


def answer_metrics(claims, status):
    errs = [c['primary_error_type'] for c in claims]
    return dict(
        distortion=sum(status[s] in ('wrong_asserted', 'conflict_asserted') for s in PRIMARY) / 3,
        preservation=sum(status[s] == 'correct' for s in PRIMARY) / 3,
        F=errs.count('F'), O=errs.count('O'), S=errs.count('S'), M=errs.count('M'),
        role_inflated=status['S4.role'] == 'wrong_asserted',
        hours_status=status['S3.hours'],
        propagation=sum(bool(c['error_propagation']) for c in claims),
        unsupported_numeric=sum(c['input_support'] == '근거없음' for c in claims
                                if c['attribute'] in PRIMARY and c['claim_kind'] == 'possession'),
        needs_review=sum(c['needs_review'] for c in claims),
    )


def family_mean(rows, key):
    """Average replicates within family first; families are the units."""
    by = {}
    for r in rows:
        by.setdefault(r['family_id'], []).append(float(r[key]))
    return {f: mean(v) for f, v in by.items()}


def cluster_ci(per_family, *, samples=10000, seed=20261013):
    fams = sorted(per_family)
    if not fams:
        return None
    vals = [per_family[f] for f in fams]
    rng = random.Random(seed)
    boots = sorted(mean(rng.choice(vals) for _ in vals) for _ in range(samples))
    return dict(mean=mean(vals), lo=boots[int(0.025 * samples)], hi=boots[int(0.975 * samples) - 1],
                n_families=len(vals))

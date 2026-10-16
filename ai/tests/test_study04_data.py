import json
from pathlib import Path

import pytest

from src.rag import study02_eval as ev
from src.rag import study04_data as sd

DATA = Path(r'C:\Users\bje10\OneDrive\문서\Codex\2026-10-12\new-chat\outputs\VC_AI_STUDY_04_20261015\data')


def test_world_shape_and_templates():
    w = sd.make_world()
    fams = w['families']
    assert len(fams) == 44
    assert sum(f['split'] == 'test' for f in fams) == 42
    assert sum(f['split'] == 'test' and f['job'] == 'backend' for f in fams) == 21
    assert sd.check_world(w) == []
    assert sd.make_world() == w  # deterministic


def test_existing_judge_parses_every_family():
    for f in sd.make_world()['families']:
        gold, cf = ev.gold_for(f)
        assert cf == {'S1.count': gold['S1.count'] + 9, 'S2.count': gold['S2.count'] + 15,
                      'S3.hours': gold['S3.hours'] + 60}
        assert len(gold['names']) == 3


@pytest.mark.skipif(not (DATA / 'bundle.json').exists(), reason='study04 data not built')
def test_built_bundle_counts_and_dummy_twins():
    b = json.loads((DATA / 'bundle.json').read_text(encoding='utf-8'))
    test = [e for e in b['entries'] if e['draft']['split'] == 'test']
    assert len(test) == 252 and sum(len(e['draft']['source_records']) for e in test) == 1008
    d = json.loads((DATA / 'dummy_records.json').read_text(encoding='utf-8'))
    assert sum(x['split'] == 'test' for x in d) == 1008
    texts = {r['achievement']['description'] for e in test for r in e['draft']['source_records']}
    assert {x['content'] for x in d if x['split'] == 'test'} == texts

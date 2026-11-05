"""SQL extraction and existing runner integration, without a DB or model."""
import hashlib
import json
from pathlib import Path

import pytest

from scripts.run_minimal_pipeline import main
from src.data.backend_adapter import adapt_backend_response
from src.data.backend_seed import extract_seed

USER1 = '11111111-1111-4111-8111-111111111111'
USER2 = '22222222-2222-4222-8222-222222222222'
RID1 = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa'
RID2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb'
RID3 = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc'
DID = 'did:example:seed-001'


def sql_row(table, values):
    def literal(value):
        return 'NULL' if value is None else "'" + str(value).replace("'", "''") + "'"
    return f'INSERT INTO public.{table} VALUES (' + ', '.join(map(literal, values)) + ');'


def user(uid=USER1, did=DID):
    return sql_row('users', [uid, 'test@example.com', "지원자 O'Neil", did,
                             '2026-10-01 00:00:00+00', '2026-10-01 00:00:00+00'])


def credential(rid=RID1, owner=USER1, date='2024-01-01 00:00:00+00'):
    raw = {'issuer': {'name': '테스트 기관'},
           'achievement': {'name': "SQL, O'Neil 과정", 'description': 'NULL, (괄호); 문자열'}}
    return sql_row('credentials', [rid, owner, 'mock_seeder', 'ext-' + rid, 'OB3.0',
                                   raw['achievement']['name'], '테스트 기관', 'accredited',
                                   'verified', date, None, '2024-01-01',
                                   json.dumps(raw, ensure_ascii=False), '2026-10-01 00:00:00+00'])


def seed_file(tmp_path, lines=None):
    path = tmp_path / 'seed.sql'
    if lines is None:
        lines = [user(), user(USER2, 'did:example:seed-002'), credential(),
                 credential(RID2, USER1, '2025-01-01 00:00:00+00'), credential(RID3, USER2)]
    path.write_text('\n'.join(lines) + '\n', encoding='utf-8-sig')
    return path


def test_extract_subject_preserves_text_and_matches_backend_contract(tmp_path):
    path = seed_file(tmp_path)
    before = path.read_bytes()
    payload, report = extract_seed(path, DID)
    assert payload['user_profile']['name'] == "지원자 O'Neil"
    assert [r['record_id'] for r in payload['verified_credentials']] == [RID2, RID1]
    assert payload['verified_credentials'][0]['raw_data']['achievement']['description'] == 'NULL, (괄호); 문자열'
    assert payload['verified_credentials'][0]['issued_at'] == '2025-01-01T00:00:00+00:00'
    assert report['total_users'] == 2 and report['total_credentials'] == 3
    assert report['selected_count'] == 2
    assert report['sha256'] == hashlib.sha256(before).hexdigest()
    assert report['source_provider_counts'] == {'mock_seeder': 2}
    records, adapted = adapt_backend_response(payload, DID)
    assert len(records) == 2 and adapted['rejected_count'] == 0
    assert all('verification' not in r for r in records)
    assert path.read_bytes() == before


@pytest.mark.parametrize('lines,message', [
    ([user(), user()], 'Duplicate seed user'),
    ([user(), user(USER2)], 'Duplicate seed user'),
    ([user(), credential(), credential()], 'Duplicate seed record'),
    ([user(), credential(RID1, USER2)], 'unknown user'),
    ([user(USER2, 'did:example:other')], 'not found'),
    ([user(), credential(date='2024-01-01 00:00:00')], 'timezone'),
    ([sql_row('users', [USER1])], 'column count'),
    ([user(), "INSERT INTO public.credentials VALUES (now());"], 'Unsupported SQL value'),
    ([user(), 'COPY public.credentials FROM stdin;'], 'Unsupported seed SQL'),
    ([user(), 'DELETE FROM public.credentials;'], 'Unsupported seed SQL'),
    ([user(), 'INSERT INTO public.users (id) VALUES (NULL);'], 'Unsupported seed SQL'),
    ([user(), "INSERT INTO public.users VALUES ('broken);"], 'Unsupported SQL value'),
])
def test_bad_seed_fails_closed(tmp_path, lines, message):
    with pytest.raises(ValueError, match=message):
        extract_seed(seed_file(tmp_path, lines), DID)


def test_dump_setup_is_not_executed(tmp_path):
    path = seed_file(tmp_path, ['-- dump', '\\restrict token', 'SET statement_timeout = 0;',
                              "SELECT pg_catalog.setval('public.x', 1, false);", user(),
                              credential(), '\\unrestrict token'])
    assert extract_seed(path, DID)[1]['selected_count'] == 1


def read(path):
    return json.loads(path.read_text(encoding='utf-8'))


class Tokenizer:
    def apply_chat_template(self, messages, **kwargs):
        return json.dumps(messages, ensure_ascii=False)
    def __call__(self, texts, **kwargs):
        return {'input_ids': [[1] * 100]}


def forbidden(*args, **kwargs):
    pytest.fail('Must not load a model or access BE')


def runner_args(tmp_path, seed):
    context = tmp_path / 'context.json'
    context.write_text(json.dumps({'subject_did': DID, 'profile': '지원자',
                                   'jd': '개발자', 'request': '자격 요약'}), encoding='utf-8')
    return ['--backend-seed', str(seed), '--context', str(context),
            '--output', str(tmp_path / 'runs'), '--check-only']


def test_seed_to_abc_saves_replayable_response_and_audit(tmp_path):
    seed = seed_file(tmp_path)
    args = runner_args(tmp_path, seed) + ['--record-id', RID1]
    directory = main(args, tokenizer_factory=lambda *a, **k: Tokenizer(),
                     llm_factory=forbidden, client_factory=forbidden)
    manifest = read(directory / 'manifest.json')
    assert manifest['status'] == 'checked'
    assert manifest['input_kind'] == 'backend_seed'
    assert manifest['selected_record_ids'] == {'A_no_rag': [], 'B_naive_rag': [RID1], 'C_verified_rag': []}
    assert manifest['input_sha256']['backend_seed'] == hashlib.sha256(seed.read_bytes()).hexdigest()
    assert read(directory / 'seed_selection.json')['selected_count'] == 2
    assert read(directory / 'backend/manifest.json')['source']['mode'] == 'sql_seed'
    assert read(directory / 'policy_audit.json')['input_kind'] == 'backend_seed'
    assert not list(directory.glob('*.result.json'))
    # The extracted response is directly reusable by the pre-existing input mode.
    args[:2] = ['--backend-response', str(directory / 'backend/raw_response.json')]
    replay = main(args, tokenizer_factory=lambda *a, **k: Tokenizer(), llm_factory=forbidden)
    assert read(replay / 'B_naive_rag.input.json') == read(directory / 'B_naive_rag.input.json')


def test_missing_did_saved_as_extraction_failure(tmp_path):
    args = runner_args(tmp_path, seed_file(tmp_path, [user(USER2, 'did:example:other')]))
    with pytest.raises(ValueError, match='not found'):
        main(args, tokenizer_factory=forbidden, llm_factory=forbidden)
    manifest = read(next((tmp_path / 'runs').glob('*/manifest.json')))
    assert manifest['status'] == 'failed' and manifest['stage'] == 'seed_extraction'
    assert manifest['inference_performed'] is False


def test_user_without_credentials_does_not_start_model(tmp_path):
    with pytest.raises(ValueError, match='No usable BE records'):
        main(runner_args(tmp_path, seed_file(tmp_path, [user()])),
             tokenizer_factory=forbidden, llm_factory=forbidden)
    directory = next((tmp_path / 'runs').iterdir())
    assert read(directory / 'seed_selection.json')['selected_count'] == 0
    assert read(directory / 'backend/manifest.json')['status'] == 'no_usable_records'

import copy
from pathlib import Path
import pytest
from jsonschema import ValidationError
from src.data.credential_loader import load_credentials, load_from_api, load_from_api_with_report

ROOT = Path(__file__).resolve().parents[1]
class Client:
    def __init__(self, records): self.records = records
    def credentials(self, did, status):
        assert status == 'all'
        return {'records': [{'record_id': r['record_id']} for r in self.records]}
    def record(self, rid): return next(r for r in self.records if r['record_id'] == rid)

def fixtures():
    records = load_credentials(ROOT/'data/mock/credentials.json')
    broken = copy.deepcopy(records[-1])
    broken['record_id'] = '00000000-0000-0000-0000-000000000009'
    del broken['achievement']['name']
    return records + [broken]

def test_quarantine_preserves_all_statuses_and_original():
    records = fixtures()
    original = copy.deepcopy(records)
    accepted, report = load_from_api_with_report(Client(records), records[0]['subject_did'])
    assert len(accepted) == 6
    assert {r['verification']['status'] for r in accepted} == {'verified','invalid','revoked','expired','unverifiable','pending'}
    assert report['received_count'] == report['accepted_count'] + report['rejected_count'] == 7
    rejected = report['rejected_records'][0]
    assert rejected['instance_path'] == ['achievement']
    assert rejected['original_record'] == original[-1]
    assert records == original

def test_strict_loader_still_fails():
    records = fixtures()
    with pytest.raises(ValidationError): load_from_api(Client(records), records[0]['subject_did'])

def test_cross_subject_and_duplicate_are_fatal():
    records = fixtures()[:1]
    with pytest.raises(ValueError, match='Cross-subject'):
        load_from_api_with_report(Client(records), 'did:example:other')
    with pytest.raises(ValueError, match='Duplicate'):
        load_from_api_with_report(Client(records * 2), records[0]['subject_did'])

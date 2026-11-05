"""BE response contract tests. All HTTP and generation here are test doubles."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys

import httpx
import pytest

from src.clients.backend_client import BackendClient
from src.data.backend_adapter import (BackendContractError, adapt_backend_response,
                                     make_export, load_backend_export, save_backend_export)
from src.data.credential_loader import validate_records
from src.llm.local_llm import MockLLM
from src.rag.backend_pipeline import BackendPipeline, prepare_backend_prompt
from src.rag.pipeline import build_prompt, snippet

ROOT = Path(__file__).resolve().parents[1]
DID = "did:example:be-demo-001"
DEMO = ROOT / "data/backend_demo/careers_response.json"


def payload():
    return json.loads(DEMO.read_text(encoding="utf-8"))


def test_http_to_existing_prompt_and_generate_interface():
    calls = []
    def handler(request):
        calls.append(request)
        assert request.method == "GET"
        assert request.url.raw_path == b"/api/v1/subjects/did%3Aexample%3Abe-demo-001/careers"
        assert not request.url.query and "authorization" not in request.headers
        return httpx.Response(200, json=payload())
    with BackendClient("http://be.test:3000", transport=httpx.MockTransport(handler)) as client:
        records, report = adapt_backend_response(client.careers(DID), DID)
    assert len(calls) == 1
    assert (report["received_count"], report["accepted_count"], report["rejected_count"]) == (4, 3, 1)
    assert report["accepted_status_counts"] == {"verified": 2, "revoked": 1}
    assert {r["provenance"]["raw_shape"] for r in records} == {"summary_object", "vc_payload"}
    assert records[1]["achievement"]["criteria_narrative"] == "실습 프로젝트 제출"
    assert records[1]["expires_at"] == "2027-10-01T00:00:00Z"
    assert records[1]["evidence"][0]["url"] == "https://example.com/evidence/db"
    class SpyGenerator:
        prompts = []
        def generate(self, prompt):
            self.prompts.append(prompt)
            return "TEST DOUBLE; NO INFERENCE"
    llm = SpyGenerator()
    exported = make_export(records, DID)
    result = BackendPipeline(llm).run(exported, "어떤 교육을 이수했나요?")
    expected = build_prompt("어떤 교육을 이수했나요?", [{"record_id": r["record_id"], "snippet": snippet(r)} for r in records])
    assert llm.prompts == [expected]
    assert result["prompt"] == expected
    assert len(result["retrieved_contexts"]) == 3
    for secret in ("trust_level", "signature", "backend_status", "verification_basis", "demo@example.com", "DEMO_PERSON"):
        assert secret not in result["prompt"]
    assert result["paper_evaluation_eligible"] is False
    assert all("verification" not in r for r in records)
    filtered = BackendPipeline(MockLLM()).run(exported, "교육 내역", status_filter="verified")
    assert len(filtered["retrieved_contexts"]) == 2
    assert filtered["excluded_records"][0]["backend_status"] == "revoked"
    assert "condition" not in filtered  # No accidental C_verified_rag label.


@pytest.mark.parametrize("status", [404, 401, 500, 302])
def test_http_errors_are_not_empty_success(status):
    with BackendClient("http://be.test", transport=httpx.MockTransport(lambda _: httpx.Response(status))) as client:
        with pytest.raises(httpx.HTTPStatusError):
            client.careers(DID)


def test_timeout_and_non_json_are_errors():
    def timeout(request):
        raise httpx.ReadTimeout("test", request=request)
    with BackendClient("http://be.test", transport=httpx.MockTransport(timeout)) as client:
        with pytest.raises(httpx.ReadTimeout): client.careers(DID)
    with BackendClient("http://be.test", transport=httpx.MockTransport(lambda _: httpx.Response(200, text="<html>"))) as client:
        with pytest.raises(ValueError, match="non-JSON"): client.careers(DID)


@pytest.mark.parametrize("url", ["http://be.test/api/v1", "http://user:secret@be.test", "file:///tmp/a", "http://be.test?token=x"])
def test_bad_base_urls_rejected(url):
    with pytest.raises(ValueError): BackendClient(url)


@pytest.mark.parametrize("kind", ["profile", "row", "raw", "vc_subject", "duplicate", "duplicate_uppercase"])
def test_identity_conflicts_abort_entire_batch(kind):
    data = payload()
    if kind == "profile": data["user_profile"]["did"] = "did:example:other"
    elif kind == "row": data["verified_credentials"][0]["subject_did"] = "did:example:other"
    elif kind == "raw": data["verified_credentials"][0]["raw_data"]["subject_did"] = "did:example:other"
    elif kind == "vc_subject": data["verified_credentials"][1]["raw_data"]["credentialSubject"]["id"] = "did:example:other"
    else:
        duplicate = deepcopy(data["verified_credentials"][0])
        if kind == "duplicate_uppercase": duplicate["record_id"] = duplicate["record_id"].upper()
        data["verified_credentials"].append(duplicate)
    with pytest.raises(BackendContractError): adapt_backend_response(data, DID)


@pytest.mark.parametrize("kind", ["status", "missing", "bad_date", "raw_shape", "bad_description", "title_conflict", "date_conflict", "multiple_subjects"])
def test_malformed_rows_quarantined_without_synthesizing_fields(kind):
    data = payload()
    data["verified_credentials"] = data["verified_credentials"][:2]
    row = data["verified_credentials"][0]
    if kind == "status": row["status"] = "mysterious"
    elif kind == "missing": del row["issuer"]
    elif kind == "bad_date": row["issued_at"] = "yesterday"
    elif kind == "raw_shape": row["raw_data"] = {"unexpected": 1}
    elif kind == "bad_description": row["raw_data"]["achievement"]["description"] = {"unexpected": 1}
    elif kind == "title_conflict": row["raw_data"]["achievement"]["name"] = "different"
    elif kind == "date_conflict": row["raw_data"]["issuanceDate"] = "2020-01-01T00:00:00Z"
    elif kind == "multiple_subjects": row["raw_data"]["credentialSubject"] = [{"id": DID}, {"id": DID}]
    records, report = adapt_backend_response(data, DID)
    assert len(records) == 1 and report["rejected_count"] == 1
    assert records[0]["record_id"] == data["verified_credentials"][1]["record_id"]
    assert report["rejected_records"][0]["index"] == 0


def test_empty_response_is_valid_but_not_ready(tmp_path):
    data = payload(); data["verified_credentials"] = []
    report = save_backend_export(data, DID, tmp_path / "empty", source={"mode": "test"})
    assert report["received_count"] == report["accepted_count"] == 0
    manifest = json.loads((tmp_path / "empty/manifest.json").read_text(encoding="utf-8"))
    assert manifest["status"] == "no_usable_records"
    assert load_backend_export(tmp_path / "empty/backend_evidence.json")["records"] == []


def test_failed_contract_preserves_raw_and_audit(tmp_path):
    data = payload(); data["user_profile"]["did"] = "did:example:other"
    with pytest.raises(BackendContractError):
        save_backend_export(data, DID, tmp_path / "bad", source={"mode": "test"})
    assert json.loads((tmp_path / "bad/raw_response.json").read_text(encoding="utf-8")) == data
    assert (tmp_path / "bad/validation_report.json").exists()
    assert not (tmp_path / "bad/backend_evidence.json").exists()


def test_export_does_not_overwrite_and_strict_vc_loader_stays_strict(tmp_path):
    save_backend_export(payload(), DID, tmp_path / "snapshot", source={"mode": "test"})
    with pytest.raises(FileExistsError):
        save_backend_export(payload(), DID, tmp_path / "snapshot", source={"mode": "test"})
    export = load_backend_export(tmp_path / "snapshot/backend_evidence.json")
    from jsonschema import ValidationError
    with pytest.raises(ValidationError): validate_records(export["records"])
    export["records"][0]["verification"] = {"status": "verified"}
    (tmp_path / "tampered.json").write_text(json.dumps(export), encoding="utf-8")
    with pytest.raises(ValidationError): load_backend_export(tmp_path / "tampered.json")


def test_explicit_candidates_and_status_filter_do_not_refill():
    records, _ = adapt_backend_response(payload(), DID)
    export = make_export(records, DID)
    revoked_id = records[2]["record_id"]
    result = prepare_backend_prompt(export, "교육 내역", record_ids=[revoked_id], status_filter="verified")
    assert result["candidate_record_ids"] == [revoked_id] and result["retrieved_contexts"] == []
    with pytest.raises(ValueError): prepare_backend_prompt(export, "question", record_ids=["unknown"])
    with pytest.raises(ValueError): prepare_backend_prompt(export, "question", record_ids=[revoked_id, revoked_id])


def test_offline_cli_export_and_preview(tmp_path):
    commands = [sys.executable, str(ROOT / "scripts/export_backend_credentials.py"), "--input-json", str(DEMO),
                "--subject-did", DID, "--output", str(tmp_path / "exports")]
    first = subprocess.run(commands, capture_output=True, text=True, encoding="utf-8")
    assert first.returncode == 0, first.stderr
    exported = next((tmp_path / "exports").glob("*/backend_evidence.json"))
    second = subprocess.run([sys.executable, str(ROOT / "scripts/run_backend_preview.py"), "--export", str(exported),
                             "--question", "수료한 교육은 무엇인가요?", "--output", str(tmp_path / "previews")],
                            capture_output=True, text=True, encoding="utf-8")
    assert second.returncode == 0, second.stderr
    manifest_path = next((tmp_path / "previews").glob("*/manifest.json"))
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["mock"] is True and manifest["inference_performed"] is False
    assert manifest["context_count"] == 3 and manifest["token_budget_checked"] is False
    assert "데이터베이스" in (manifest_path.parent / "prompt.txt").read_text(encoding="utf-8")

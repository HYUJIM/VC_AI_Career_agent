import copy
import hashlib
import json

import pytest
from jsonschema import ValidationError

from src.rag.paper_vc_adapter_v2 import (
    ALL_CHECKS,
    REQUIRED_CHECKS,
    audit_export,
    audit_records,
    write_report,
)


@pytest.fixture
def credential():
    timestamp = "2026-10-01T06:23:14.280Z"
    return {
        "record_id": "6fbc4737-f004-5b04-a32f-0f0d4308599f",
        "subject_did": "did:fixture:student-001",
        "schema_version": "0.1.0",
        "normalized_at": timestamp,
        "source": {
            "provider": "dx_ledger_badge",
            "external_id": "cred-ob2-signed-002",
            "fetched_at": timestamp,
        },
        "format": "OB2.0",
        "issuer": {
            "did": "did:fixture:acme-university",
            "name": "ACME University",
            "trust_level": "accredited",
        },
        "achievement": {
            "id": "https://example.org/achievements/signed-002",
            "name": "데이터베이스 설계 인증",
        },
        "raw_hash": "a" * 64,
        "verification": {
            "status": "verified",
            "checks": [
                {
                    "check": name,
                    "result": "skip" if name == "anchor_match" else "pass",
                    "checked_at": timestamp,
                }
                for name in ALL_CHECKS
            ],
            "proof": {
                "type": "VC-JWT",
                "alg": "EdDSA",
                "verification_method": "did:fixture:acme-university#key-1",
            },
            "anchor": {
                "computed_hash": "b" * 64,
                "anchored_hash": None,
                "match": None,
            },
            "verified_at": timestamp,
            "verifier_version": "vc-core-mock/0.1.0",
        },
    }


def set_result(record, name, result):
    for check in record["verification"]["checks"]:
        if check["check"] == name:
            check["result"] = result
            return
    raise AssertionError(f"Check not found: {name}")


def first_audit(record):
    return audit_records([record])["records"][0]


def save_export(tmp_path, records):
    path = tmp_path / "credentials.json"
    path.write_text(
        json.dumps(records, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def test_accepts_signed_record_and_preserves_identifiers(credential):
    report = audit_records([credential])
    audit = report["records"][0]

    assert report["summary"] == {
        "total": 1,
        "eligible": 1,
        "excluded": 0,
    }
    assert report["paper_ready"] is False
    assert audit["eligible"] is True
    assert audit["reasons"] == ["signed_fixture_policy_satisfied"]
    assert audit["record_id"] == credential["record_id"]
    assert audit["source"] == credential["source"]
    assert audit["achievement_id"] == credential["achievement"]["id"]
    assert audit["reported_hashes"] == {
        "raw_hash": "a" * 64,
        "computed_hash": "b" * 64,
        "anchored_hash": None,
    }
    assert audit["reported_verification"] == credential["verification"]
    assert report["eligible_records"] == [credential]
    assert audit["unavailable_evidence"]
    assert any("anchor_match=skip" in item for item in audit["limitations"])


def test_signature_skip_is_excluded_as_policy_mismatch(credential):
    set_result(credential, "signature", "skip")
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert "check_not_accepted:signature:skip" in audit["reasons"]
    assert (
        "verified_without_policy_pass:signature:skip"
        in audit["policy_mismatches"]
    )
    # A hosted credential's skip alone does not prove an engine error.
    assert audit["engine_inconsistencies"] == []


@pytest.mark.parametrize("name", REQUIRED_CHECKS)
def test_each_required_fail_is_excluded(credential, name):
    set_result(credential, name, "fail")
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert f"check_not_accepted:{name}:fail" in audit["reasons"]
    assert (
        f"verified_with_failed_check:{name}"
        in audit["engine_inconsistencies"]
    )


@pytest.mark.parametrize("result", ["warn", "skip"])
def test_required_non_pass_is_excluded(credential, result):
    set_result(credential, "did_resolution", result)
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert f"check_not_accepted:did_resolution:{result}" in audit["reasons"]


@pytest.mark.parametrize(
    "status",
    ["revoked", "expired", "invalid", "unverifiable", "pending"],
)
def test_non_verified_status_is_excluded_even_with_pass_checks(
    credential, status
):
    credential["verification"]["status"] = status
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert f"status_not_verified:{status}" in audit["reasons"]


def test_revoked_record_preserves_both_exclusion_reasons(credential):
    credential["verification"]["status"] = "revoked"
    set_result(credential, "revocation", "fail")
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert "status_not_verified:revoked" in audit["reasons"]
    assert "check_not_accepted:revocation:fail" in audit["reasons"]


@pytest.mark.parametrize("name", ALL_CHECKS)
def test_missing_check_is_excluded_by_record_audit(credential, name):
    credential["verification"]["checks"] = [
        check
        for check in credential["verification"]["checks"]
        if check["check"] != name
    ]
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert f"missing_check:{name}" in audit["reasons"]


def test_duplicate_check_is_excluded_without_choosing_a_result(credential):
    duplicate = copy.deepcopy(credential["verification"]["checks"][2])
    duplicate["result"] = "fail"
    credential["verification"]["checks"].append(duplicate)
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert "duplicate_check:signature" in audit["reasons"]
    assert "verified_with_invalid_check_structure" in (
        audit["engine_inconsistencies"]
    )


def test_unexpected_check_is_excluded_by_record_audit(credential):
    credential["verification"]["checks"].append({
        "check": "unexpected_check",
        "result": "pass",
        "checked_at": "2026-10-01T06:23:14.280Z",
    })
    audit = first_audit(credential)

    assert audit["eligible"] is False
    assert "unexpected_check:unexpected_check" in audit["reasons"]


def test_check_order_does_not_change_classification(credential):
    expected = first_audit(credential)
    credential["verification"]["checks"].reverse()
    actual = first_audit(credential)

    assert actual["eligible"] == expected["eligible"]
    assert actual["reasons"] == expected["reasons"]
    assert actual["reported_verification"]["checks"] == (
        credential["verification"]["checks"]
    )


@pytest.mark.parametrize("result", ["pass", "skip", "fail", "warn"])
def test_anchor_policy(credential, result):
    set_result(credential, "anchor_match", result)
    audit = first_audit(credential)

    assert audit["eligible"] is (result in {"pass", "skip"})


def test_duplicate_record_id_blocks_collection(credential):
    with pytest.raises(ValueError, match="Duplicate record_id"):
        audit_records([credential, copy.deepcopy(credential)])


def test_mixed_subjects_block_collection(credential):
    other = copy.deepcopy(credential)
    other["record_id"] = "047d9ce2-64f3-56a6-b96a-8a1a2d1f262f"
    other["subject_did"] = "did:fixture:student-002"

    with pytest.raises(ValueError, match="Cross-subject"):
        audit_records([credential, other])


def test_input_is_unchanged_and_outputs_are_independent(credential):
    records = [credential]
    original = copy.deepcopy(records)
    report = audit_records(records)

    assert records == original

    report["records"][0]["source"]["external_id"] = "changed"
    report["records"][0]["reported_verification"]["checks"][0]["result"] = "fail"
    report["eligible_records"][0]["achievement"]["name"] = "changed"

    assert records == original


def test_summary_counts_every_record_once(credential):
    excluded = copy.deepcopy(credential)
    excluded["record_id"] = "047d9ce2-64f3-56a6-b96a-8a1a2d1f262f"
    set_result(excluded, "signature", "skip")

    report = audit_records([credential, excluded])

    assert report["summary"] == {
        "total": 2,
        "eligible": 1,
        "excluded": 1,
    }
    assert len(report["records"]) == 2
    assert [item["record_id"] for item in report["eligible_records"]] == [
        credential["record_id"]
    ]


def test_export_uses_real_loader_and_hashes_exact_input_bytes(
    tmp_path, credential
):
    path = save_export(tmp_path, [credential])
    before = path.read_bytes()

    report = audit_export(path)

    assert report["summary"]["eligible"] == 1
    assert report["input_export"]["sha256"] == hashlib.sha256(before).hexdigest()
    assert report["input_export"]["byte_count"] == len(before)
    assert report["input_export"]["path"] == str(path.resolve())
    assert path.read_bytes() == before
    assert report["paper_ready"] is False


def test_export_supports_utf8_bom(tmp_path, credential):
    path = tmp_path / "bom.json"
    path.write_text(json.dumps([credential]), encoding="utf-8-sig")

    report = audit_export(path)

    assert report["summary"]["eligible"] == 1
    assert report["input_export"]["sha256"] == (
        hashlib.sha256(path.read_bytes()).hexdigest()
    )


@pytest.mark.parametrize("malformation", ["missing", "duplicate", "unexpected"])
def test_strict_loader_blocks_malformed_check_exports(
    tmp_path, credential, malformation
):
    checks = credential["verification"]["checks"]
    if malformation == "missing":
        checks.pop()
    elif malformation == "duplicate":
        # Keep seven entries but replace expiration with a duplicate schema.
        checks[-1] = copy.deepcopy(checks[0])
    else:
        checks[-1]["check"] = "unexpected_check"

    path = save_export(tmp_path, [credential])

    with pytest.raises((ValueError, ValidationError)):
        audit_export(path)


def test_export_loader_blocks_duplicate_record_ids(tmp_path, credential):
    path = save_export(tmp_path, [credential, copy.deepcopy(credential)])

    with pytest.raises(ValueError, match="Duplicate record_id"):
        audit_export(path)


def test_export_blocks_mixed_subjects(tmp_path, credential):
    other = copy.deepcopy(credential)
    other["record_id"] = "047d9ce2-64f3-56a6-b96a-8a1a2d1f262f"
    other["subject_did"] = "did:fixture:student-002"
    path = save_export(tmp_path, [credential, other])

    with pytest.raises(ValueError, match="Cross-subject"):
        audit_export(path)


def test_report_is_written_and_existing_output_is_preserved(
    tmp_path, credential
):
    report = audit_records([credential])
    output = tmp_path / "audit.json"
    write_report(report, output)
    before = output.read_bytes()

    assert json.loads(output.read_text(encoding="utf-8")) == report

    with pytest.raises(FileExistsError):
        write_report(report, output)

    assert output.read_bytes() == before
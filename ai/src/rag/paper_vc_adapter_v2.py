"""Audit reported VC results under the signed-fixture paper policy.

This adapter does not perform cryptographic verification.
The existing credential loader and VC engine remain unchanged.
"""

import argparse
import copy
import hashlib
import json
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from src.data.credential_loader import load_credentials


REQUIRED_CHECKS = (
    "schema",
    "did_resolution",
    "signature",
    "issuer_trust",
    "revocation",
    "expiration",
)
ALL_CHECKS = (
    "schema",
    "did_resolution",
    "signature",
    "issuer_trust",
    "revocation",
    "anchor_match",
    "expiration",
)

# These evidence artifacts have not been supplied to this audit.
# Reported pass results alone do not establish possession of them.
UNAVAILABLE_EVIDENCE = (
    "original_signed_credentials_and_signature_bytes",
    "resolved_did_documents_and_verification_keys",
    "reproducible_cryptographic_verifier_execution_logs",
    "issuer_trust_registry_snapshot",
    "revocation_registry_snapshot",
    "verification_time_and_expiration_evaluation_logs",
    "ledger_anchor_transaction_and_inclusion_evidence",
    "real_operational_issuer_credentials",
)


def audit_record(record):
    """Classify one record without mutating its contents."""
    verification = record.get("verification", {})
    if not isinstance(verification, dict):
        verification = {}

    checks = verification.get("checks", [])
    reasons = []
    policy_mismatches = []
    engine_inconsistencies = []

    if not isinstance(checks, list):
        checks = []
        reasons.append("checks_not_array")

    named_checks = {}
    names = []

    for index, check in enumerate(checks):
        if not isinstance(check, dict):
            reasons.append(f"malformed_check:{index}")
            continue
        name = check.get("check")
        if not isinstance(name, str):
            reasons.append(f"invalid_check_name:{index}")
            continue
        names.append(name)
        named_checks.setdefault(name, []).append(check)

    counts = Counter(names)
    if len(checks) != len(ALL_CHECKS):
        reasons.append(f"check_count:{len(checks)}")

    for name in sorted(counts):
        if counts[name] > 1:
            reasons.append(f"duplicate_check:{name}")
        if name not in ALL_CHECKS:
            reasons.append(f"unexpected_check:{name}")

    for name in ALL_CHECKS:
        if name not in named_checks:
            reasons.append(f"missing_check:{name}")

    # A duplicated check is never resolved by choosing one result.
    for name in ALL_CHECKS:
        entries = named_checks.get(name, [])
        if len(entries) != 1:
            continue

        result = entries[0].get("result")
        allowed = ("pass", "skip") if name == "anchor_match" else ("pass",)
        if result not in allowed:
            reasons.append(f"check_not_accepted:{name}:{result}")

        if verification.get("status") == "verified":
            if result == "fail":
                engine_inconsistencies.append(
                    f"verified_with_failed_check:{name}"
                )
            if result not in allowed:
                policy_mismatches.append(
                    f"verified_without_policy_pass:{name}:{result}"
                )

    status = verification.get("status")
    if status != "verified":
        reasons.append(f"status_not_verified:{status}")

    if status == "verified" and any(
        reason.startswith((
            "checks_not_array",
            "malformed_check:",
            "invalid_check_name:",
            "check_count:",
            "duplicate_check:",
            "unexpected_check:",
            "missing_check:",
        ))
        for reason in reasons
    ):
        engine_inconsistencies.append("verified_with_invalid_check_structure")

    source = record.get("source", {})
    if not isinstance(source, dict):
        source = {}

    anchor = verification.get("anchor", {})
    if not isinstance(anchor, dict):
        anchor = {}

    limitations = [
        "Reported checks are audited; signatures are not reverified.",
        "Eligibility is limited to this signed-fixture research policy.",
        "Reported hashes are preserved, not independently validated.",
        "Reported expiration and revocation reflect the original check time.",
    ]
    anchor_entries = named_checks.get("anchor_match", [])
    if (
        len(anchor_entries) == 1
        and anchor_entries[0].get("result") == "skip"
    ):
        limitations.append(
            "anchor_match=skip is permitted; ledger anchoring is unconfirmed."
        )

    eligible = not reasons
    return {
        "record_id": record["record_id"],
        "subject_did": record["subject_did"],
        "source": copy.deepcopy(source),
        "achievement_id": record.get("achievement", {}).get("id"),
        "reported_hashes": {
            "raw_hash": record.get("raw_hash"),
            "computed_hash": anchor.get("computed_hash"),
            "anchored_hash": anchor.get("anchored_hash"),
        },
        "reported_verification": copy.deepcopy(verification),
        "eligible": eligible,
        "reasons": reasons if reasons else ["signed_fixture_policy_satisfied"],
        "policy_mismatches": policy_mismatches,
        "engine_inconsistencies": engine_inconsistencies,
        "unavailable_evidence": list(UNAVAILABLE_EVIDENCE),
        "limitations": limitations,
    }


def audit_records(records):
    """Audit records, blocking ambiguous identity or subject collections.

    File ingestion additionally uses load_credentials for schema validation.
    """
    if not isinstance(records, list):
        raise ValueError("Expected a list of credential records")

    seen = set()
    subjects = set()

    for record in records:
        if not isinstance(record, dict):
            raise ValueError("Credential record must be an object")
        record_id = record.get("record_id")
        subject = record.get("subject_did")
        if not isinstance(record_id, str) or not record_id:
            raise ValueError("Missing or invalid record_id")
        if not isinstance(subject, str) or not subject:
            raise ValueError("Missing or invalid subject_did")
        if record_id in seen:
            raise ValueError("Duplicate record_id")
        seen.add(record_id)
        subjects.add(subject)

    if len(subjects) > 1:
        raise ValueError("Cross-subject credential collection prohibited")

    audits = [audit_record(record) for record in records]
    eligible_records = [
        copy.deepcopy(record)
        for record, audit in zip(records, audits)
        if audit["eligible"]
    ]

    return {
        "audit_version": "paper-vc-adapter-v2",
        "policy": {
            "name": "signed-fixture-v2",
            "required_pass_checks": list(REQUIRED_CHECKS),
            "anchor_match_allowed": ["pass", "skip"],
            "required_status": "verified",
            "check_order_matters": False,
            "cryptographic_verification_reexecuted": False,
            "scope": "test_fixture_research",
        },
        "paper_ready": False,
        "subject_did": next(iter(subjects)) if subjects else None,
        "summary": {
            "total": len(records),
            "eligible": len(eligible_records),
            "excluded": len(records) - len(eligible_records),
        },
        "records": audits,
        "eligible_records": eligible_records,
        "unavailable_evidence": list(UNAVAILABLE_EVIDENCE),
        "limitations": [
            "This audit does not establish operational VC authenticity.",
            "Pre-ingestion quarantined records are outside these counts.",
            "Eligibility counts are not hallucination reduction metrics.",
            "Paper readiness requires further evidence and experiments.",
        ],
    }


def audit_export(input_path):
    """Load through the existing loader and bind the audit to input bytes."""
    path = Path(input_path)
    before = path.read_bytes()
    records = load_credentials(path)
    after = path.read_bytes()

    if before != after:
        raise ValueError("Input export changed during loading")

    # Ensure the loaded records correspond to the bytes being hashed.
    if records != json.loads(before.decode("utf-8-sig")):
        raise ValueError("Loaded records differ from the input export")

    report = audit_records(records)
    report["input_export"] = {
        "path": str(path.resolve()),
        "sha256": hashlib.sha256(before).hexdigest(),
        "byte_count": len(before),
    }
    report["audited_at"] = datetime.now(timezone.utc).isoformat()
    return report


def write_report(report, output_path):
    """Exclusive creation prevents overwriting any existing output."""
    path = Path(output_path)
    payload = json.dumps(
        report, ensure_ascii=False, indent=2, allow_nan=False
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(payload)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Audit reported VC results under the signed-fixture policy."
    )
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)

    output = Path(args.output)
    if output.exists():
        parser.error(f"Output already exists; choose a new path: {output}")

    try:
        report = audit_export(args.input)
        write_report(report, output)
    except (OSError, ValueError) as error:
        parser.exit(1, f"Audit failed: {error}\n")

    summary = report["summary"]
    print(f"Total: {summary['total']}")
    print(f"Eligible: {summary['eligible']}")
    print(f"Excluded: {summary['excluded']}")
    print(f"Paper ready: {report['paper_ready']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())